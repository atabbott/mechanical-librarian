# Readiness packet

Keep the proposal short enough to evaluate in one pass:

- canonical packet ID/digest, source target/ref, and authoritative provenance;
- source item and current update marker;
- ordered deliverable list and dependency graph when more than one delivery is
  required;
- proposed delivery classification and rationale;
- observable outcome in one sentence;
- supported demonstration;
- acceptance criteria, one testable behavior per item;
- realistic failure behavior and required test layer;
- dependencies and decisions;
- a concise dependency, library, or tool evaluation only when the materiality
  trigger in `dependency-evaluation.md` passes;
- branch and pull-request boundary;
- documentation, migration, compatibility, changelog, or safety work only when
  materially applicable; and
- disposition plus exact next action.

Keep analyst streams separate from the root synthesis. For each stream record
its bounded question, binding/thread, source evidence, and uncertainty. Record
the readiness review's primary lens, isolated thread, result, and any same-
thread targeted continuation; do not embed prior conclusions in its raw packet.

For a functional-slice tracker, include its exact constituent-ticket closure
list and exit criteria. For each constituent ticket, include one primary slice,
complete acceptance criteria, required test layer, dependencies, and the
observable tracker outcome it supports. Provide issue-ready titles and bodies.

Prefer the fewest coherent vertical deliverables. Do not create separate
deliverables merely for domain types, repositories, adapters, migrations, API,
CLI, tests, or documentation when they jointly implement one outcome.

Do not add implementation design unless it constrains acceptance, enumerate
hypothetical exceptions, or mark work Ready while a material decision remains.
Leave routine package selection to delivery. When an evaluation is material,
record its trigger, baseline, smallest credible candidate set, recommendation,
delivery constraints, implementation-time revalidation, and exact unresolved
decision without creating a separate permission ticket.

When external tracking is approved, append the exact approved issue/handoff set
and bounded mutation/readback plan. This approval evidence is not part of the
initial independent review packet and never authorizes unlisted work.
