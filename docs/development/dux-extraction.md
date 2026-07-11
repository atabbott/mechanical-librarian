# DUX Prototype Extraction Record

## Purpose

This record defines how the completed Dashboard Usability Redesign (DUX) work
from the Ebook Management prototype informs Mechanical Librarian. It is an
evidence and provenance record, not permission to create a runtime dependency
on the prototype.

The [prototype extraction policy](prototype-extraction.md) remains
authoritative for every copied or adapted component.

## Frozen source baseline

| Property | Value |
| --- | --- |
| Prototype | Ebook Management workspace |
| Source commit | `358d70039a2d7da5e1d4cf7f19032ed5bf0fdc3f` |
| Source tag | `dux-complete-2026-07-10` |
| Source change | Complete DUX review dashboard redesign |
| DUX backlog | `docs/dashboard-usability-redesign-tickets.md` in the prototype |
| Runtime dependency allowed | No |

The source revision is local to the prototype repository at the time this
record was written. If it is later published or transferred, this record must
be updated with the durable source URL without adding a personal filesystem
path.

## Post-DUX operational-learning baseline

Later prototype revisions provide additional operating evidence without
changing the frozen DUX source baseline above:

| Source commit | Captured learning |
| --- | --- |
| `28cb81e73a2a475c3c2935bdefad2472f56b4771` | Review-evidence reconciliation, title-candidate reliability, and readiness verification |
| `c5b2fa8005e09e9c4a5d9d065c72e182aed45c81` | Checkpointed full-run orchestration and independently verified completion |
| `13d219d81281ace1aab13ba79f055cfec5b27eb5` | Series-label reconciliation, projected-candidate provenance, and review-summary count semantics |
| `0a05639d9a38037a513d7e38b5f4bf357927293c` | Dependency-aware unattended orchestration, durable resume, and bounded progress reporting |
| `fd2685450c985a8c213602888470ef53d79abd5b` | Adverse reviewer-problem polarity, series review, and proposal-version default refresh |
| `0c1f953256397d616d35350cccc993f397287f1c` | Candidate-lane accounting and reuse, workflow upgrades, candidate aggregation, and browser qualification |
| `cfb014c46dd6fb0c4356381b91b8845fcc6f642b` | Authoritative current/evidence packet joins, content-ISBN edition evidence, and JSONL framing |

These commits are an operational-learning baseline, not a production
architecture, module boundary, or replacement for
`358d70039a2d7da5e1d4cf7f19032ed5bf0fdc3f`. Mechanical Librarian preserves
the following contracts from them:

- a multi-stage review run has durable stage checkpoints, can resume without
  repeating completed external work, and cannot advance when required scope is
  incomplete or unverified or when an upstream version is stale; typed terminal
  no-result and degraded optional outcomes remain valid inputs;
- readiness is a typed, scoped statement of coverage over the selected
  targets, not the existence of an artifact or a global ingestion gate, and a
  blocker identifies the required scope, stage, target, and missing or failed
  terminal outcome;
- agreement is measured across independent evidence families, so repeated or
  projected records from one provider do not manufacture consensus;
- independence is not a universal two-family rule: a decisive single-family
  result remains possible when target identity is supported and direct,
  authoritative, edition-applicable evidence satisfies the field policy;
- candidate selection must establish target and work identity; edition identity
  or applicability is additionally required when the field policy or proposed
  change is edition-sensitive. Values equivalent to current state are omitted,
  and identity or comparable-identifier conflicts relevant to the field reject
  the candidate;
- provider failure is a typed operational outcome, not negative metadata
  evidence and not support for a competing candidate; and
- AI completion is independently audited against the selected targets, exact
  supplied proposal IDs, and authoritative upstream evidence and provider
  outcomes. Required inputs need an accepted terminal outcome. An optional
  provider failure may be a typed terminal operational outcome without blocking
  unrelated ingestion or becoming negative evidence;
- a run or stage may persist a nonterminal `waiting` state while bounded
  dependencies are incomplete. Scheduling persists and durably enqueues the
  complete eligible set, capacity-bounded workers claim stable job identities,
  and fan-in accepts one monotonic-fenced independently validated outcome per
  job. At-least-once redelivery is expected; idempotent handlers and provider
  idempotency or reconciliation prevent or recover duplicate external effects;
- restart and resume inspect durable job identity and outcomes rather than
  process IDs. Report-only unattended continuation retains an immutable
  initiating principal and authorization context, scope, policy and schema
  versions, resource bounds, cancellation, and expiry. Each actual transition
  actor is audited and linked to that authority; resume cannot widen authority
  or turn observation into apply permission;
- qualified, versioned series-label mappings preserve the raw provider label,
  lineage, work identity, rule version, and replay or supersession history;
  unknown labels remain unchanged;
- a source AI recommendation carried with projected shared evidence is
  provenance only. It is not destination evidence, consensus, disposition, or
  authority, and the destination independently requalifies identity,
  applicability, current state, evidence, and policy; and
- review reporting distinguishes target count, ordinary and destructive case
  counts, field work-item count, and planned-change count rather than using one
  ambiguous total;
- the required reviewer `problems` list contains only unresolved or adverse
  problem codes. A clean approval uses `problems: []`; supporting facts remain
  in typed evidence references and the concise reason;
- every authoritative reviewer packet joins the exact bounded normalized
  current snapshot, explicit loaded, missing, or unavailable field state,
  relevant evidence, and candidate and policy versions before invocation;
- a versioned, capability-derived lane manifest records applicability for every
  derived lane. Applicable lanes record a typed execution outcome; only a
  successfully completed applicable lane records candidate count, which may be
  zero, and execution mode `executed` or `reused`. Reuse requires exact target
  scope, matching input fingerprints, compatible provider, query,
  configuration, and contract versions, and validated complete expected
  artifacts. A missing lane is a coverage failure, while failed execution is
  never reusable and follows governed retry, backoff, and suppression;
- each run pins an immutable workflow-definition version and graph digest.
  Incompatible definition changes create linked superseding or replanned work
  whose affected downstream checkpoints are transitively marked ineligible for
  reuse or current applicability; original history is never mutated or erased;
- one distinct checksum-valid ISBN with typed format- and edition-applicable
  content provenance can be strong edition evidence only when a materially
  independent, format-appropriate provider returns the same canonical ISBN
  (including ISBN-10/ISBN-13 equivalence) and work, target, and media identity
  agree. Multiple content ISBNs, unrelated previews or other-work claims,
  correlated providers, and edition, format, narration, abridgement,
  dramatization, omnibus, or cross-media conflicts remain unresolved;
- collapsing an identical normalized candidate retains each source assessment,
  lineage, and contradiction. Deterministic policy recomputes the aggregate
  rather than unioning every risk code or selecting a strongest row; and
- JSONL interoperability artifacts use physical record delimiters, so Unicode
  line and paragraph separators inside JSON strings do not create records.
  Real-browser qualification derives categories from the versioned product and
  category-capability contract, covers a representative fixture for each
  applicable category, records explicit reasons for not-applicable categories,
  captures page and console errors, and verifies proposal-version-bound
  presentation defaults.

A successful command or output file is insufficient. When proposal selection
proves exact target coverage and yields zero proposals, orchestration records a
terminal no-review outcome and does not invoke the reviewer. Every packet that
is invoked requires exact per-proposal output coverage.

For AI output, `manual` remains reviewer-result semantics only: it means that
multiple specifically supported alternatives remain unresolved. It does not
create a human decision, imply general uncertainty, or establish apply
readiness. A valid `manual` result may satisfy reviewer-output completeness for
its proposal while leaving human resolution outstanding.

The prototype's agent controller, generated static dashboard, self-editing
skill behavior, service-specific pipeline topology, hard-coded fusion weights
and production-learned aliases, OS process IDs or detached watchers as durable
authority, free-form run briefs as policy, and JSON encoded inside a schema
string are explicitly excluded. So are prototype provider topology, in-place
mutation of historical workflow graphs, broad host-language line splitting,
browser-local authority, and any production title, alias, identifier, path, or
report. Mechanical Librarian implements the retained contracts through its
application services, durable jobs, adapter-neutral domain types, persisted
audit records, and supported interfaces.

## Verification at capture

The frozen DUX working tree passed:

- 188 discovered prototype tests;
- 64 focused review-case, export, queue, responsive, replacement, audit, and
  approval tests;
- the Python import-boundary check;
- the maintained command-index check; and
- `git diff --check`.

After staging, 12 focused contract, export, and service-replacement tests were
run again before the source commit. These results prove the captured prototype
behavior; they do not qualify that implementation for production reuse.

## Extraction classification

| DUX slice | Mechanical Librarian treatment | Destination |
| --- | --- | --- |
| DUX-1 baseline audit and fixtures | Preserve metrics and re-author sanitized structural fixtures | WP0 quality foundation |
| DUX-2 through DUX-8 review-case semantics | Reimplement as domain, evidence, and policy contracts | WP1, WP4, WP5 |
| DUX-9 service export safety | Translate to immutable `ApplyPlan` construction | WP7 |
| DUX-10 through DUX-17 review interaction | Adopt as product and accessibility acceptance criteria | WP9 |
| DUX-18 performance | Preserve goals; replace static payload solution with paginated APIs | WP9 |
| DUX-19 regression coverage | Port scenarios into domain, API, component, and real-browser tests | WP0, WP9 |
| DUX-20 and DUX-21 replacement rollout | Reference rollout and compatibility lessons only | WP9, release engineering |
| DUX-22 linked sibling groups | Reimplement as a display/query projection | WP1, WP9 |
| DUX-23 artifact cleanup | Exclude; it is prototype maintenance only | None |

## Module assessment

### Behavior eligible for selective extraction

- `review_case_contracts.py`: target-shaped cases, stable structural
  validation, display-only groups, typed cover/series choices, and destructive
  case separation. Reimplement identity and lifecycle for snapshot-aware,
  adapter-neutral persisted entities.
- `review_case_exports.py`: explicit choice validation, field applicability,
  rejected-choice exclusion, cover evidence gates, per-field provenance, and
  conflicting-selection failure. Translate these rules into application-owned
  `ApplyPlan` construction rather than copying the JSONL exporter.
- `combined_dashboard_data.py`: selected pure normalization, consensus,
  applicability, notice classification, and evidence-reference behavior.
  Extract only after each behavior has a narrow contract and synthetic fixture.
- DUX fixtures and tests: preserve adversarial scenarios such as repeated
  source rows, evidence-only rows, conflicting candidates, independent
  siblings, unloaded current state, cover/series choices, no-ops, unsupported
  fields, and separate destructive cases.

### Reimplementation required

- The target identity must include configured adapter/library scope and the
  immutable item snapshot or proposal-set version. The prototype's
  `system + target_id` hash is insufficient.
- Human decision states must map to the production lifecycle instead of copying
  `accepted`, `skipped`, and `blocked` as one browser state machine.
- Policy-authorized automatic work and human-authorized unresolved work must
  both create explicit apply provenance. The prototype's blanket human export
  gate conflicts with the product's automation policy.
- Bulk preview, commit, reversal, and audit must be persisted and guarded by
  case versions and idempotency keys.
- Queue pagination, evidence detail, history, and summaries must come from the
  versioned API.
- Accessibility and responsive requirements need component and real-browser
  verification rather than only static renderer-string assertions.

### Excluded implementation

- `build_combined_library_dashboard.py` as a generated static application;
- browser-local workflow state and state-key migration;
- whole-library embedded browser payloads;
- browser-created JSONL as authoritative approval;
- the mixed workflow/data/presentation structure of
  `combined_dashboard_data.py` as a module boundary;
- service-specific legacy renderer replacement code;
- historical run paths, reports, source-line references, and saved state;
- DUX-23 retention and cleanup executors; and
- CWA implementation until a supported production adapter is separately
  approved.

The post-DUX exclusions additionally cover the agent controller, generated
static dashboard, self-editing skill behavior, service-specific orchestration
topology, hard-coded candidate-fusion weights and author- or title-specific
series aliases, detached process watchers, process-local job identity,
free-form reviewer instructions, and nested JSON string workarounds recorded
above.

## Preserved behavioral invariants

- At most one ordinary active review case exists per target snapshot.
- Evidence-only input cannot create an actionable empty case.
- Identical choices retain consensus without appearing multiple times.
- An unsafe field cannot suppress a separately safe field.
- Rejected, inapplicable, no-op, and contradicted alternatives cannot enter an
  apply plan.
- AI recommendation, human decision, disposition, and readiness are distinct.
- Duplicate/removal work stays separate and destructive.
- Confirmed siblings remain independent for selection, decision, validation,
  and apply.
- Evidence is stored once and referenced by ID.
- Initial presentation state cannot invent a human decision.

The [review experience](../product/review-experience.md) is authoritative when
it intentionally differs from prototype behavior.

## Fixture and privacy rules

No production report, title, author, cover, identifier, path, domain, database,
or saved browser state may be copied into this repository.

Each imported fixture must:

- be generated or irreversibly sanitized;
- carry a source-scenario note rather than a production source path;
- contain the smallest facts needed to prove the behavior;
- use synthetic target, evidence, proposal, and run identifiers;
- remove external URLs unless the URL shape itself is under test; and
- pass repository secret, path, licence, and copyright checks.

Golden files represent stable contracts, not prototype formatting.

## Licence and attribution

No prototype source file has been copied by this documentation change. Before
the first code or fixture extraction:

1. Complete the Mechanical Librarian licence decision.
2. Confirm authorship and third-party dependency compatibility for the exact
   source lines or fixture facts.
3. Record whether code was copied, adapted, or independently reimplemented.
4. Preserve attribution or source history where the selected licence requires
   it.
5. Document rejected prototype behavior and the tests proving the new boundary.

## Required pull-request extraction note

Every DUX-derived implementation pull request records:

- source commit and tag from this document;
- DUX ticket or scenario being preserved;
- target package and public contract;
- behavior copied, adapted, reimplemented, or rejected;
- fixture sanitization and licence conclusion;
- parity, failure, architecture-boundary, and security tests; and
- confirmation that the built product has no runtime access to the prototype.
