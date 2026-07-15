# Structured delivery records

Use a durable record for every PR queue and every run that uses multiple
reviewers, crosses a session or compaction, reaches a remediation cycle, waits
on an external gate, or performs repository, Project, or other external
mutation. Create it before the first required event. Never reconstruct a clean
history after the fact.

Version 3 deliberately separates structural validity from completion:

```text
python3 .codex/scripts/validate_team.py --record <team-run.json>
python3 .codex/scripts/validate_team.py --record <team-run.json> --assert-qualified
```

The first command proves that the record is parseable and internally coherent.
Only `--assert-qualified` may support a completion claim. It rejects legacy
records, empty `DELIVERED` records, incomplete obligations, stale policy
bundles, failed or stale gates, and evidence not bound to the final target ref.

Create a new record with `--init-run`. Version 1 and 2 records remain readable
but are read-only and never qualify. Continue one without rewriting it:

```text
python3 .codex/scripts/validate_team.py \
  --upgrade-record <v2/team-run.json> \
  --upgrade-output <new-v3/team-run.json>
```

The upgrade copies only representable claims and adds an `UNAVAILABLE`
`UPGRADE_RECONCILIATION` obligation. Record inherited external mutations and
any known protocol or execution-policy violation explicitly, then resolve the
reconciliation with direct evidence. The source remains unchanged.

Discover or resume records by workflow and target:

```text
python3 .codex/scripts/validate_team.py --index-runs --json
python3 .codex/scripts/validate_team.py --resume-run \
  --workflow maintain-open-prs --target-kind pull_request --target-id 123
```

Discovery indexes every `team-run.json` below `--runs-root`, rejects duplicate
run IDs, and returns exactly one resumable match. PR queues use one `targets`
entry and one final delivery per PR; queue-wide evidence never substitutes for
target/ref evidence.

## Frozen control and exact identity

`policy_bundle` hashes every workflow skill body and every required shared
policy body. `team_context` binds that digest to the frozen manifest, config,
root, eligible roles, and workflow. Structural validation reports later drift;
qualification requires the current bundle and context.

Record the repository base/head and tree, and versioned `artifacts` for the
repository, tree, package, module, wheel, document, or other tested unit.
`base_tree` and `head_tree` are the SHA-256 of the exact Git tree-OID text;
qualification re-derives both from the trusted current worktree rather than
trusting the packet's worktree path or self-declared hashes. Every
check records exact artifact IDs, target/ref, start/end time, command, result,
invalidation rule, and a canonical environment identity (platform, runtime,
container image, and lock digest). Evidence state is one of `FRESH`,
`INHERITED`, `NOT_APPLICABLE`, `UNAVAILABLE`, or `STALE`. Required unavailable
or stale evidence never qualifies. Package/module/wheel identity may be N/A
only through an exact skip owned by `ARTIFACT_IDENTITY:<KIND>`.

`bindings` are invocation-scoped by unique binding ID, target, and ref; the same
requested role may have several bindings. They attest role, model, reasoning,
and permissions independently as `VERIFIED`, `DECLARED`, `FALLBACK`, or
`UNKNOWN`, with receipt provenance, plus proof that every required capability
is present and every forbidden capability absent. Every eligible role has one
final-ref `BINDING` obligation, which may cite multiple qualified invocation
bindings. A role that did not apply uses an exact N/A skip. Declared, unknown,
weaker, or capability-incomplete identity cannot source a qualified PASS.

## Obligations, skips, and review cycles

Every duty is a versioned `obligation` scoped to one target and ref. The kinds
are binding, criteria, review, check, qualification, external gate, mutation,
connector, delivery, and upgrade reconciliation. `SATISFIED` cites durable
evidence IDs. `NOT_APPLICABLE` and `UNAVAILABLE` cite a matching
`skip_manifest` entry with exact test ID, platform, reason, owning gate,
authority, and time. Unavailable blocks qualification; N/A must be justified.

Each criterion is unique by ID and normalized text. It is `PASS` with current
evidence or justified `NOT_APPLICABLE` with a skip. Duplicate or contradictory
criteria, partial/failing criteria, and PASS evidence from an unknown binding
fail qualification.

Each independent review records reviewer role, exact task, distinct lens,
context mode, packet digest, target/ref and stage, start/end, result, evidence,
and finding IDs. `FRESH_ISOLATED` means a raw frozen packet in a fresh
`fork_turns=none` context and therefore has no originating thread.
`TARGETED_CONTINUATION` uses a new binding at the resulting ref and names the
exact originating review/binding/thread. `REPLACEMENT_ISOLATED` uses a new
binding and thread only when the original review is proven `UNAVAILABLE`, with
complete origin lineage and a full affected-charter review. Every eligible
reviewer has exactly one final-ref review obligation or an explicit N/A skip.
A satisfied review cites its exact qualified invocation binding. For Git-backed
writer workflows, qualification independently re-derives the packet base,
target tree, diff, name-status, repository blobs, and restricted run
requirements from that trusted worktree.

Stage events form complete per-target/per-ref cycles. `I0` and `PR0` are the
first implementation and PR stages; later ordinals are contiguous. Connector
rounds are only `C1` then optional `C2`. `C0`, `C3+`, repeated, reversed, or
skipped ordinals are invalid.

Findings separate the root's `decision` from lifecycle `status`. Add a finding
once, then update its decision, status, family, miss reason, detail, and
evidence; do not overwrite its source. This permits later family expansion
without pretending the earlier decision was a status. Qualification requires
all findings resolved and no pending, `FIX_NOW`, or `NOT_READY` decision.

## Writers, connectors, and mutations

Events are append-only, versioned, contiguous, timezone-aware, and scoped when
they describe target work. Phase and stage transitions must be legal; in
particular `DELIVERED` cannot return to review. Writer intervals cannot overlap,
and every applied repository mutation must fall inside its actor's interval.
When root takes over a writer workflow, a target/ref/packet-bound
`ROOT_ESCALATION` event must precede root's writer start and therefore precede
the first root edit. Record historical misses as an
`EXECUTION_POLICY_VIOLATION`; never hide them by rewriting history.

Connector records preserve automatic `C1` and the single manual `C2` plus
round-start, pickup-added, pickup-unobserved, pickup-removed, and terminal
observations. Each event embeds the canonical read-only helper report and its
SHA-256, then projects the exact actor, anchor, external ID, target/ref,
timestamp, signal, and latency. Pickup removal compares the earlier report that
contained the recorded `eyes` ID with the terminal report where it is absent.
`PROTOCOL_VARIANCE` and `INCONSISTENT` are durable states and violations, not
clean results.
The round `started_at` is the immutable request creation time. Its first helper
report retains the later real observation time and exact positive latency; it
is never backdated to manufacture a zero-latency observation.

External evidence records source PR, exact head, observation date,
invalidation rule, and state. Every required external gate is represented even
when pending. A pending gate is valid only for an explicitly ended
`AWAITING_EXTERNAL` handoff; blocked or unavailable required gates fail.

Every Project mutation records the issue-number-to-item-ID precondition,
mutation ID, intended before/after states, observed readback, and a passing
unintended-change check. Repository and other external mutations record exact
before/after refs, external ID, actor, result, evidence state, and timestamp.

## Qualification and delivery

For every target, the final gate requires complete binding, criteria, check,
qualification, external-gate, and delivery obligations; exact repository/tree
and applicable package/module/wheel identity; current final-ref checks and
reviews; no failed/stale checks, open violations, or unresolved findings; and
one `QUALIFICATION_RECORDED` event after the last relevant review, check, or
mutation.

Delivery is per target. Defect, focused-change, and slice workflows require a
non-null branch, commit, and pull request. PR maintenance requires a pull
request or queue handoff. Read-only preparation and exploration use documented
or report-only delivery. Delivery ref and status must match the final qualified
target and the run's `DELIVERED` or `AWAITING_EXTERNAL` state.

Use `.codex/scripts/run_record.py append --json ...` for exact v3 objects; every
update is written atomically only after structural validation. `finalize` binds
the target final ref and repository head/tree. Structural write success is not
qualification: always run the explicit semantic gate before reporting
completion.
