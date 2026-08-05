# Shadow-review qualification evidence

Private qualification evaluates the qualification-only shadow reviewer without
changing its product authority. It separates whether an attempt executed from
whether a returned recommendation was valid and useful, and it scores only
actions admitted by the exact frozen policy compiler.

This contract applies to private synthetic and sanitized qualification
evidence. It does not add a product retry loop, a production reviewer, decision
authority, or write authority.

Deterministic shadow-decision candidates use the same separation of exposed
development and unseen fresh evidence. Score exact installed-artifact output
against the frozen accepted-action projection; keep construction validity,
semantic validity, nonmanual precision and recall, wrong and unsupported
actions, sensitive-field errors, case utility, and deterministic replay as
separate gates. Production-data mechanism checks establish compatibility and
representativeness but are not precision labels unless independently
adjudicated. A semantic fresh failure is terminal evidence and is never a
transport retry or a tuning input.

## Freeze before scoring

Before a fresh cohort is scored, retain canonical bytes and SHA-256 identities
for the compiler catalogue and policy, packet, prompt, output schema,
qualification profile, treatment, cohort, scheduled order, and scoring rules.
The compiler uses packet and policy facts only. Cohort selection and ordering
must not use gold decisions, accepted-action projections, prior reviewer
outputs, scores, latency, titles, human identity, or response metadata.

Development-exposed material may be used to develop and test a candidate. It
must not be reported as fresh qualification evidence. Once a fresh batch has
been scored, its compiler, scorer, policy, cohort, order, and treatment are not
tuned. A failed holdout becomes development material; a revised candidate
requires a new unseen holdout.

## Attempt and execution views

Every planned cell has an append-only attempt lineage. Each attempt binds:

- the planned cell and attempt identity;
- exact packet, prompt, output-schema, profile, and treatment bytes or their
  bound digests;
- requested model, effort, invocation, and scheduled and actual order;
- raw process streams and their digests when retention is permitted, or an
  explicit unavailable state; and
- one typed terminal receipt.

Qualification reports the following views separately:

- **first pass**: the first immutable attempt for each planned cell;
- **retry pairs**: the original and its authorized replacement, without
  erasing either observation;
- **attempt-inclusive**: every dispatched or attempted execution;
- **execution-inclusive intent to treat (ITT)**: every planned cell, including
  cells without a semantically scoreable result; and
- **terminal semantic**: at most one terminal semantic observation per
  case, cell, and repeat, selected from the highest frozen revision.

Dispatch completion validity and reviewer semantic-output validity are distinct
denominators. A completed process can return an invalid semantic document. A
process failure or timeout is not a semantic recommendation and is never
imputed as one.

Terminal kinds are closed: semantic result, ordinary timeout, process failure,
transport failure, cancellation, resource failure, or receipt-integrity
failure. Semantic results, including adverse results, and ordinary timeouts at
the retained boundary are terminal evidence. Only a predeclared and bounded
process, transport, cancellation, resource, or receipt-integrity failure may be
retried. Every retry remains append-only, consumes the owning stage's reserve,
and retains its first-pass pair. Reserve is never used to retry semantic
outcomes or ordinary timeouts.

## Action-level scoring

The unit of semantic scoring is one logical opportunity and its exact action.
The scorer accepts only an action present in the exact frozen compiler
catalogue. It retains the proposal, destination, candidate, action, typed
exclusion, policy, catalogue, publication, manifestation, and field-policy
lineage needed to reproduce the score.

Scoring keeps two questions separate:

1. **Exact form:** did the result name the exact expected frozen action?
2. **Policy acceptability:** was the named catalogue action one of the
   owner-accepted safe outcomes for that opportunity?

This allows more than one safe action where policy permits it without treating
one gold string as the only acceptable output. It also prevents the scorer from
repairing an inadmissible or missing compiler action.

Representable opportunities are evaluated against catalogue actions. Unknown,
conflicting, unrepresentable, or compiler-missing opportunities fail closed to
manual or abstention evidence. The scorer does not infer a candidate, redirect
an action to another destination, project an accepted action into the
catalogue, or treat syntactic validity as policy acceptance. Publication event
and precision, manifestation scope, language and publisher policy, paired
destination routing, and bibliographic-combination closure remain part of the
bound action semantics.

Aggregate quality reports state their denominators and keep at least these
measures separate: semantic validity, exact-form accuracy, policy-acceptable
accuracy, nonmanual precision, unsupported-action rate, manual or abstention
rate, utility, and category-specific weakness. Execution failures do not enter
the semantic-quality numerator or denominator, but remain visible in ITT and
execution-reliability views.

## Identity, privacy, and reporting

Requested response identity is distinct from provider-observed identity.
Provider model, route, tier, effort, fallback, usage, and cost are recorded only
as `observed`, `not_exposed`, or `unverifiable`; missing fields are never
inferred. An attribution limitation does not by itself make dispatch invalid.

Public reports are aggregate-safe. They contain counts, rates, frozen version
identities, and gate dispositions, but no private case rows, titles, source
text, raw model output, paths, credentials, provider state, or human identity.
Private receipts and manifests remain owner-only and append-only.
