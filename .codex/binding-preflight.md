# Agent binding and task-packet preflight

Apply this preflight before every delegated task. Request the exact role from
`.codex/team.toml` and compare the runtime result with the frozen declaration.
Allocate one unique `binding_id` per delegated invocation, scoped to its target
and ref. Multiple invocation bindings may request the same role. Record role,
model, reasoning effort, permissions, required/forbidden capabilities, and
thread ID independently under `.codex/structured-records.md`; evidence for one
dimension never proves another.

## Binding decision

A required participant qualifies only when every required dimension is
`VERIFIED`, or when an explicit `FALLBACK` records both the requested and actual
value with provenance. Null, unknown, or wrong values are not silently accepted.

- A fresh or replacement reviewer needs a unique non-null thread, the declared Sol model and effort
  or an explicitly recorded same-or-stronger fallback, and verified read-only
  permissions. Stop if actual permissions are writable or cannot be established.
- A targeted continuation uses a new invocation binding at the resulting ref
  but the exact originating reviewer thread.
- A reader fallback must remain read-only. Keep the work with root when that
  boundary cannot be established.
- A writer needs verified workspace permission and the sole-writer lease before
  editing. A fallback writer must be named and recorded before its interval.
- One runtime thread may not act as two roles. A reviewer thread may not be a
  worker, root, or another reviewer thread.

`DECLARED` and `UNKNOWN` are valid historical attestations but cannot satisfy a
required final-ref binding obligation. When a role cannot qualify, record the
exact fallback or unavailable obligation and stop the affected gate; do not
invent identity from the requested name.

## Capability-based reviewer fallback

Treat the requested reviewer name as a task charter and capability boundary,
not as a requirement that the host runtime expose the same narrator label. A
generic or root-labelled host does not by itself qualify or disqualify a
reviewer. What matters is whether this invocation is a distinct review thread
with no writer history or inherited conclusions and with the requested
capabilities and restrictions.

An isolated CLI invocation may therefore bind as the explicit actual role
`isolated_cli_reviewer` with `FALLBACK` attestations when all of these are
recorded:

- a generated launch receipt and runtime receipt name the binding ID, requested and actual
  role, exact packet digest, thread, explicit model and reasoning, ignored user
  configuration, approval policy, verified read-only sandbox, every required
  capability as present, every forbidden capability as absent, and provenance;
- the runtime independently exposes a unique non-null thread ID and verifies
  the enforced read-only boundary;
- the task declares `FRESH_ISOLATED`, no originating thread, and no prior
  conclusions, remediation history, or named green-test outcomes; and
- that thread has never held a root or worker lease and is not reused for any
  other role.

Record launch-derived model and reasoning values as `FALLBACK`, not
`VERIFIED`, when the runtime cannot query them independently. Record read-only
permissions as `VERIFIED` only from runtime enforcement evidence. The host's
generic label is provenance for the fallback; it is not evidence that this
unique context-free thread is the orchestrator's thread. Block when the launch
receipt is missing or ambiguous, the thread is reused, context is inherited,
or write authority cannot be disproved.

## Canonical raw review packet

Before creating an initial reviewer, build one canonical packet containing:

1. packet version and ID, workflow/run/target IDs, frozen target ref, base ref,
   repository/tree and applicable artifact IDs;
2. requested reviewer role, frozen role-config fingerprint, primary lens, and
   exact charter task;
3. authoritative requirement revisions and raw requirement text, governing
   repository and external-contract paths, and the raw changed source/diff;
4. current evidence and check IDs with exact artifact and environment identity;
5. deliberately excluded areas and the output contract; and
6. an exclusion declaration confirming that the packet contains no worker or
   orchestrator conclusions, prior findings or verdicts, family dispositions,
   remediation history, iteration framing, or named green-test summaries.

Canonicalize the packet as UTF-8 JSON with sorted keys and compact separators,
compute its SHA-256 digest before spawn, and record that digest unchanged in the
review task and v3 review record. If the packet, target ref, lens, role charter,
or evidence identity changes, create a new packet version and digest.

Use the tracked helper rather than hand-normalizing or hashing a draft:

```text
python3 .codex/scripts/review_packet.py freeze \
  --draft <draft.json> --output <packet.json>
python3 .codex/scripts/review_packet.py verify \
  --packet <packet.json> --digest <sha256>
python3 .codex/scripts/review_packet.py receipt \
  --packet <packet.json> --digest <sha256> --output <receipt.json> \
  <runtime identity, permission, and capability evidence>
```

The helper validates the task, target, explicit capability fallback, exclusion
declarations, role-charter mapping, and safe path shape before atomically
writing canonical bytes. Dispatch only its reported digest, and populate a
qualified runtime binding only from the generated receipt.

## Context isolation and continuation

Create every initial independent reviewer with `fork_turns="none"`. Record
`FRESH_ISOLATED`, the packet digest, and no originating thread. The spawn prompt
contains only the canonical raw packet plus the role's own configured
instructions; never fork the root, worker, or another reviewer history.

Every fresh `publication_challenger` uses a new isolated thread. Its own bounded
targeted verification may reuse only its exact original thread. Its packet also
omits `.codex/review-method.md`, `.codex/finding-family-audit.md`, and all review
synthesis.

Targeted re-review is the only normal review continuation. Create a new binding
at the repaired ref, send the bounded repair to the exact original reviewer
thread, record `TARGETED_CONTINUATION` plus the originating review, binding, and
thread IDs, and preserve the original lens unless the family policy requires a
materially different technique. If that thread is proven unavailable, record
the original review `UNAVAILABLE` and use `REPLACEMENT_ISOLATED`: a new
role-equivalent binding/thread, complete origin lineage, and a full affected-
charter review. This governed replacement is the only unavailable-thread path;
do not shop for approval or invent a maintainer waiver.

Run `python3 .codex/scripts/validate_team.py` after team configuration changes
or when role loading appears broken. Always run its record qualification mode
before a completion claim; configuration validity alone does not attest a
runtime binding.
