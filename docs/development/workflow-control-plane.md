# Delivery control plane

The tracked `.codex` tree is the canonical source for Mechanical Librarian's
delivery workflows, specialist declarations, evidence schemas, and deterministic
control scripts. Changes to these files are ordinary reviewed repository changes;
local copies must not become an unreviewed second policy source.

## Tracked and private boundaries

Track reusable controls:

- workflow skills and their one-level references;
- role and capability declarations;
- team, review, concurrency, escalation, and evidence policies;
- schemas and deterministic helper scripts; and
- tests that prove structural wiring and semantic completion gates.

Never track runtime records, private idea dossiers, machine paths, private test
resource locations, credentials, caches, or session logs. `.codex/.gitignore`
defines that private boundary. A local `.git/info/exclude` entry may hide the
tree in an existing checkout, but it does not replace the tracked canonical
files; use explicit staging for reviewed control changes in such a checkout.

## Local qualification

Run the complete control-plane gate from the repository root:

```text
python3 .codex/scripts/validate_team.py --self-test
python3 .codex/scripts/validate_team.py --json
python3 -m unittest discover -s .codex/tests -p 'test_*.py'
python3 -m py_compile .codex/scripts/*.py
```

The ordinary validator proves declarations and record structure. It does not
claim that delivery completed. A run may claim completion only when its current
version-3 record passes:

```text
python3 .codex/scripts/validate_team.py \
  --record .codex/runs/<run-id>/team-run.json \
  --assert-qualified
```

Version-1 and version-2 records remain readable historical evidence. Upgrade
creates a separate version-3 continuation and never turns missing historical
evidence into a qualification claim.

The run-record helper is an atomic public interface to version-3 records. Its
typed `check`, `criterion`, `handoff`, binding, finding, delivery, and
`finalize` commands emit the exact current schema or fail without changing the
record. The `event` command records ordered review stages, qualification,
connector observations, family expansion, and other lifecycle evidence. Do not
use ad hoc file editing to work around a rejected command.

Freeze and verify context-isolated reviewer packets with
`.codex/scripts/review_packet.py`. It is the canonical serializer for packet
IDs, exact staged, committed, or non-Git artifact targets, role charters,
invocation binding IDs, generated capability receipts, raw artifact
declarations, exclusions, and packet SHA-256; hand-authored JSON is a draft,
not dispatchable review evidence.
Qualification uses the repository containing the control plane as its trusted
root and re-derives Git trees, diffs, name-status, repository blobs, and
restricted run requirements there. A packet cannot select a substitute
worktree or qualify through consistently recomputed but forged hashes.

## Independent review contract

Initial independent reviewers and publication challengers use fresh threads
with `fork_turns: "none"`. Their canonical packet contains the frozen target,
raw requirements and artifacts, one primary attack lens, and a SHA-256 digest;
it excludes worker conclusions, previous verdicts, remediation history, and
named green tests. A reviewer may still report any substantiated blocker.

Runtime role, model, effort, permission, and thread evidence must be verified or
recorded as an explicit safe fallback. An unavailable custom role is not silently
treated as successfully bound. Targeted re-review uses a new binding at the
resulting ref, the exact original reviewer thread, and the smallest affected
packet. Proven thread loss uses `REPLACEMENT_ISOLATED`, a new binding/thread,
complete origin lineage, and a full affected-charter packet; fresh reviews and
challenges always use new threads.

## Evidence and external state

Bind checks and reviews to the exact final revision, artifact identity,
environment, and observation time. Classify evidence as `FRESH`, `INHERITED`,
`NOT_APPLICABLE`, or `UNAVAILABLE`; inherited evidence records its source and
invalidation rule. Expected skips name the exact test, platform, reason, and
owning gate.

Connector pickup and completion are separate events. A two-minute unobserved
pickup ends the current wait but not the review request. Later invocations
refresh state. Off-target terminal results and inconsistent reactions are
reported explicitly rather than converted to clean or left pending forever.
Every connector event embeds and hashes the exact canonical read-only helper
report; qualification recomputes its state and cross-checks the PR/comment
anchor, actor, signal, head, target, raw event ID, and timing.

Review preflight accepts only packet schema version 2. The packet identifies a
nonsymlink, worktree-local immutable snapshot of a structurally valid
version-3 run record by exact raw-byte SHA-256 and run ID. Each qualification or
gate declaration points to one check, review, or external-gate entry in that
snapshot and independently declares its exact command or operation, artifact
IDs, environment digest, and invalidation rule. Preflight resolves and
cross-checks those fields, target/ref, result, evidence state, timing, artifact
environment, reviewer binding, and external source identity. Missing entries,
snapshot drift, stale policy, substituted commands or artifacts, and
off-target evidence fail closed. Reports include command and invalidation-rule
digests rather than potentially private command text.

Packet schema version 1 and free-form `PASS` assertions are deliberately not
accepted as qualification evidence. Create a frozen snapshot before running
preflight; later append-only changes belong in a new snapshot and packet so the
original proof remains reproducible.

## Changing the controls

Use a tracked issue and focused branch for material workflow, schema, helper, or
CI changes. Run adversarial tests for false completion, stale evidence,
capability drift, context contamination, connector protocol variance, and unsafe
path handling. Forward-test complex changes in fresh read-only threads using raw
artifacts rather than the intended conclusion.
