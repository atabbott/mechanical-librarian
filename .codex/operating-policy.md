# Workflow operating policy

Use the smallest control tier that safely fits the work.

## Tiers

- **Tier 0** — read-only research, explanation, or planning. No durable run
  record or independent review is required unless the user asks for one.
- **Tier 1** — one bounded repository change. Use one writer, current checks,
  and the review tasks named by the workflow. Record concise final evidence.
- **Tier 2** — high-risk, multi-session, repeatedly expanding, or externally
  mutating work. Add safety review, durable evidence, and a fresh challenger.

Choose Tier 2 for credentials, authorization, hostile parsing, persistence or
migrations, production writes, destructive operations, concurrency/recovery,
or a second material expansion of the same finding family. Size alone does not
raise the tier.

## Profiles and task charters

Profiles define authority; task charters define purpose.

- `reader`: read-only investigation, research, triage, or reproduction;
- `worker`: sole bounded workspace writer;
- `reviewer`: fresh read-only acceptance, quality, requirements, or safety
  review; and
- `challenger`: fresh high-effort publication challenge for Tier 2 work.

Do not create a new profile merely to name a new specialty. Give `reader` or
`reviewer` an explicit task, required capabilities, scope, and lens instead.
Use separate fresh reviewer invocations when independence matters.

The root is trusted to launch profiles and record observed thread and sandbox
details. Those fields are attestations, not cryptographic proof. Never label a
runtime property independently verified when the runtime does not expose it.

## Scope and writing

Freeze the requested outcome and exclusions before editing. One worker writes
at a time; readers and reviewers may run in parallel when they do not observe a
moving target. Preserve unrelated user changes.

Do not fold an unrelated discovery into the current change. Record it in the
handoff evidence and create follow-up tracking only when repository policy or
the user requires it.

Changes confined to repository workflow skills and their `.codex` controls do
not require a tracking ticket. Product, release, dependency, security,
architecture, schema, and CI changes retain the repository's normal tracking
rules.

## Evidence and qualification

For Tier 1 and Tier 2, preserve enough evidence to compare future runs:

- workflow, tier, target, base and final ref;
- writer profile and observed thread, when delegated;
- exact check commands, results, and final ref;
- review task, packet digest, observed thread, result, and findings;
- connector state when a pull request is opened; and
- delivery URL and final ref.

Use `.codex/scripts/run_record.py validate <record>` for a durable JSON record.
A record is supporting evidence, not an authentication boundary. Qualification
requires passing checks, no unresolved blocking finding, required review tasks
at the final ref, and matching delivery evidence.

## Stopping rule

Run one initial review wave. Apply one coherent remediation cohort and send one
targeted re-review to the originating thread for each accepted finding family.
After that:

- a substantiated remaining blocker stops delivery;
- an unrelated or optional improvement is deferred without expanding scope;
- unavailable optional review is recorded and handed off; and
- unavailable required evidence blocks the claim rather than starting a new
  reviewer-search loop.

Do not add control-plane features during qualification. When the frozen
criteria, required checks, and required reviews pass, publish or hand off.
