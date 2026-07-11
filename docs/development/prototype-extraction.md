# Prototype Extraction Policy

## Purpose

The earlier Ebook Management workspace proved workflows and contains useful
logic, but it is not the architectural base of Mechanical Librarian. Extraction is
selective and evidence-driven.

## Rule

Do not copy a prototype module merely because it works in one environment.
First identify the behavior worth preserving, encode that behavior in portable
fixtures and tests, then decide whether to extract, adapt, or reimplement.

Only a committed, immutable source revision can provide extraction provenance.
Uncommitted observations may motivate independently authored generic
requirements or synthetic test categories, but they are not extraction or
fixture sources and must not contribute observed titles, identifiers, paths,
records, thresholds, or implementation details.

The completed DUX dashboard work has a frozen source revision, verification
evidence, inclusion matrix, and explicit exclusions in the
[DUX extraction record](dux-extraction.md). The production
[review-experience contract](../product/review-experience.md) is authoritative
where it intentionally differs from the prototype.

Later prototype work is recorded separately in that extraction record as a
post-DUX operational-learning baseline. It supplies scenarios and invariants
for durable orchestration, coverage-based readiness, evidence-family
independence, and independently audited AI completion; it does not supersede
the frozen DUX revision or make later prototype structure authoritative.

## Extraction checklist

A component may be extracted only when:

- its licence and authorship are compatible with this repository's licence;
- it contains no personal paths, domains, identifiers, reports, or credentials;
- its public contract is narrow and documented;
- service-specific behavior is behind the correct adapter boundary;
- filesystem and environment assumptions are configurable;
- report-only and write behavior are explicit;
- errors are typed rather than inferred from printed output;
- it has isolated unit and failure tests;
- it meets current typing, linting, security, and documentation gates;
- copied history or attribution is preserved where required.

## Prefer extraction for

- pure normalization and comparison functions;
- bounded EPUB and audio evidence parsing;
- cover dimension and quality primitives;
- provider response normalization;
- Audiobookshelf API payload and verification knowledge;
- review-plan validation rules;
- fingerprints, idempotency concepts, and failure vocabularies;
- stage-checkpoint, coverage-readiness, and evidence-family invariants; and
- sanitized structural fixtures.

## Prefer reimplementation for

- large workflow scripts with mixed concerns;
- hard-coded local paths and environment discovery;
- static dashboards and browser-local workflow state;
- console-output parsing between stages;
- direct database snapshot assumptions;
- code coupling CWA and Audiobookshelf throughout the core;
- service-specific pipeline topology and hard-coded evidence weights;
- an agent controller or a controller that edits its own operating skill;
- human approval gates embedded into candidate generation;
- code without clear provenance or safe reusable fixtures.

## Extraction record

Each extracted component should have a short record in its pull request:

- prototype source and revision;
- preserved behavior;
- rejected prototype behavior;
- new package and contract;
- fixture sanitization statement;
- licence and attribution conclusion;
- tests proving parity and new safety behavior.

DUX-derived pull requests also cite the frozen prototype commit and tag recorded
in [the DUX extraction record](dux-extraction.md). Do not replace that revision
with a filesystem path or an uncommitted workspace description.

Pull requests preserving a post-DUX operational scenario cite the applicable
full source commit from the separate post-DUX baseline in that record and state
which behavior was reimplemented. They must not present those commits as a new
DUX baseline.

## Runtime independence

The production project must build, test, install, migrate, and operate with no
checkout, import, volume, state file, or runtime call into the prototype
workspace.
