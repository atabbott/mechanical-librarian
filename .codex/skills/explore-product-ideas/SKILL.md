---
name: explore-product-ideas
description: Collaboratively explore, challenge, research, and develop early Mechanical Librarian product ideas, then preserve approved private idea dossiers and propose how mature ideas fit the roadmap, delivery slices, focused work, deferred backlog, or rejection. Use for prompts such as "I have an idea", "wouldn't it be useful if", "help me think through", "could this work", feasibility questions, possible future capabilities, awkward workflows without a settled solution, or ideas that need evidence and discussion before readiness preparation. Do not use for already Ready work, implementation, active pull-request remediation, defect or bug delivery, private security reports, or incidents.
---

# Explore product ideas

Act as a challenging, constructive Mechanical Librarian thought partner. Help
the owner discover what the idea should become; do not assume the first framing
or an implementation ticket is the answer.

Read before substantive analysis:

- `AGENTS.md`
- `docs/product/vision.md`
- `docs/product/scope.md`
- `docs/product/roadmap.md`
- `docs/product/delivery-slices.md`
- `docs/development/work-management.md`
- `.codex/standing-authority.md`
- `.codex/model-selection.md`
- `.codex/engineering-judgment.md`
- `.codex/binding-preflight.md`
- `.codex/concurrency.md`

Read the relevant architecture, security, operations, product, and development
contracts as the idea becomes concrete. Inspect current repository code and
read-only GitHub issue or Project state when either affects feasibility or plan
fit. Say when current GitHub state is unavailable instead of treating repository
planning documents as necessarily current.

## 1. Begin read-only

Start with conversation, repository inspection, and research only. Do not
create an idea dossier merely because the user mentioned an idea. When the idea
is worth retaining, propose a purpose-based dossier name and ask for approval
to create it.

Store approved private working records under `.codex/ideas/` in this checkout.
Maintain `.codex/ideas/INDEX.md` and one Markdown dossier per idea using
`references/idea-dossier.md`. Before every first write in a checkout, verify
that a prospective file beneath `.codex/ideas/` is ignored and that no file in
that directory is tracked. If either check fails, stop and ask the owner to
choose a safe private location or explicitly approve a local exclude change;
never rely on the folder name alone or silently modify ignore rules. Treat the
verified location as private local material, but never record credentials,
production data, personal paths, or other prohibited content. Mention that
checkout-local records and local exclude rules do not survive a fresh clone and
need separate backup or re-establishment when that matters to the user.

After dossier creation is approved, update it as the exploration develops
without seeking approval for every edit. Call out material changes in direction,
status, recommendation, or plan fit. Obtain explicit approval before creating
or modifying GitHub items, authoritative repository documents, or any external
system. Do not mutate external tracking during unapproved exploration. Once the
owner approves an exact tracking or handoff set, the root may perform the
ticket, relationship, and Project-field synchronization permitted by standing
authority without field-by-field confirmation.

Do not implement the idea or turn exploration into delivery work.

## 2. Frame and challenge collaboratively

First reflect the idea in its strongest plausible form. Ask small thematic
batches of questions, tightening to one question at a time around consequential
decisions. Adapt the cadence to the conversation.

Develop and test:

- the underlying problem, beneficiary, and desired observable improvement;
- evidence that the problem exists and its importance now;
- assumptions, constraints, non-goals, and success or failure signals;
- the strongest objection, opportunity cost, and reasons not to proceed;
- simpler approaches, adjacent problems, and alternative explanations; and
- what new evidence or changed constraint would reverse the current view.

Challenge the idea rather than the person. Explain why a question or objection
matters, acknowledge useful uncertainty, and help reshape a weak proposal when
a better version may preserve its intent. Do not manufacture objections or
force premature convergence.

Periodically summarize the current shared understanding, disagreements,
unknowns, and next question. Let the idea evolve instead of binding the user to
their opening wording.

## 3. Research feasibility proportionately

Inspect the codebase and current plans before claiming the product can or cannot
support an idea. Browse current external sources when feasibility depends on
changing facts such as licences, model redistribution, provider capabilities,
hardware, container images, standards, security guidance, or product terms.
Prefer primary sources and record source title, URL, access date, and the claim
it supports.

Keep these categories explicit in discussion and in the dossier:

- **Verified fact**: supported by current repository evidence or a cited source;
- **Inference**: a conclusion drawn from stated evidence;
- **Assumption**: material but not yet verified;
- **Preference or decision**: the owner's chosen direction or constraint; and
- **Open question**: evidence or judgment still needed.

For each realistic option, consider user value, product-contract fit,
architecture and trust boundaries, privacy and security, operating cost,
resource requirements, packaging and deployment, compatibility, reversibility,
dependencies, qualification burden, and ongoing maintenance. Apply only the
dimensions material to the idea.

Do not confuse technical possibility with a suitable product choice. Do not
turn early exploration into detailed implementation design unless a detail is
necessary to test feasibility or distinguish options.

### Delegate bounded research only when useful

Keep the root responsible for the conversation, questions, synthesis,
recommendation, dossier, lifecycle state, and handoff. Do not delegate opening
framing, user-preference decisions, or final plan fit.

Use the read-only `idea_researcher` role when the idea has at least two
materially independent evidence streams, a bounded repository or history search
can proceed independently, or current external research would materially
improve the next discussion. Stay root-only for a simple single-stream question
or when another voice would add coordination without new evidence.

Before spawning, apply `.codex/binding-preflight.md` and
`.codex/concurrency.md`: request `idea_researcher` by its exact configured name,
record role, model, effort, permissions, and thread independently, record the
runtime slot limit, reserve one slot for the root, and run at most three researchers
concurrently. Assign each researcher one non-overlapping evidence question with
the minimum context needed. Do not disclose the preferred answer. Require
concise verified facts, inferences, assumptions, conflicts, alternatives, open
questions, and primary-source citations with access dates.

Bind every delegated evidence task to `idea_researcher`, which declares
`gpt-5.6-terra` at medium effort and read-only permissions. If the custom role
is unavailable, keep that research with the root and disclose that delegation
was skipped; do not substitute a generic agent whose read-only permissions and
model/effort cannot be bound. Null or wrong identity/authority is an explicit
unavailable delegation or qualified fallback, never silent success. A merely
declared value is not verified runtime participation.

Reconcile researcher results against the source material before presenting
them. Resolve contradictions explicitly and keep uncertainty visible. Treat
researchers as evidence gatherers, not independent approval or readiness
reviewers; `prepare-ready-work` owns the later independent review gates.

## 4. Develop options and plan fit

Keep one dossier while alternatives address the same underlying decision.
Split a linked dossier only when a branch can progress independently, and ask
before doing so.

Compare the smallest useful option, credible alternatives, and doing nothing.
Make trade-offs and evidence gaps visible. Recommend a direction when the
evidence supports one, while preserving rejected or superseded paths and their
reasons.

Propose, but do not silently enact, the best current work-plan disposition:

- adapt or extend planned work only when it preserves that outcome's coherent
  acceptance boundary;
- propose a new future functional slice for an independently demonstrable,
  cross-cutting product outcome;
- propose focused standalone work when repository classification requires it;
- defer it to unassigned backlog work when sequencing or evidence is immature;
- park it pending a named condition or decision; or
- reject the current form, then explore an adaptation if useful.

Do not force an idea into an active slice, hide new scope inside an existing
ticket, or treat a suggested fit as authoritative. Identify prerequisites,
conflicts, opportunity cost, and likely displacement of planned work. If the
idea conflicts with the product contract, explain the conflict and explore it
openly, but treat changing the contract as a deliberate governance,
architecture, security, or ADR decision rather than a workaround.

## 5. Maintain the dossier

Keep the current synthesis concise and rewrite it as understanding changes.
Preserve dated research, decisions, discarded paths, and session history rather
than overwriting the reasoning trail. Update `INDEX.md` with title, state,
updated date, one-line summary, and dossier link.

Conclude a meaningful session by proposing one state for owner confirmation:

- `CONTINUE EXPLORING`;
- `AWAIT EVIDENCE OR DECISION`;
- `PARK FOR LATER`;
- `REJECT IN CURRENT FORM`; or
- `READY TO HAND OFF`.

Phrase the state as a recommendation and ask the owner to confirm or revise it.
Do not present or record a state as confirmed until they agree. Record parked
and rejected ideas rather than deleting them. Name the condition that would
justify reopening them.

After readiness preparation returns, use one terminal archive state when
applicable:

- `HANDED OFF` when approved readiness work now owns the idea; or
- `COVERED BY EXISTING WORK` when current authoritative work already covers the
  confirmed outcome and no amendment is needed.

Record the handoff date, resulting tracker or issue links, disposition, and any
remaining follow-up in the dossier and index. If readiness preparation proposes
an amendment or new work that still awaits approval, keep `READY TO HAND OFF`
and record the pending decision rather than implying ownership transferred.

## 6. Hand mature ideas to readiness preparation

Use `READY TO HAND OFF` only when the dossier contains a coherent desired
outcome, material decisions and constraints, the evidence behind the current
direction, credible alternatives, known unknowns, and a proposed plan fit. It
need not contain issue-ready acceptance criteria; readiness preparation owns
that conversion.

Prepare a compact handoff summary from the dossier and ask whether to invoke
`prepare-ready-work`. Pass the dossier path, current synthesis, confirmed
decisions, unresolved questions, cited evidence, proposed disposition, and any
explicitly rejected scope. Treat the proposed fit as an input to revalidate,
not a binding classification.

Leave authoritative planning-document changes, ticket decomposition, issue
creation, Project updates, and delivery workflow selection to
`prepare-ready-work` and its exact-set approval gate. After that approval,
Project synchronization follows standing authority without separate
field-by-field permission. If the owner declines handoff, retain the dossier in
its confirmed state.
