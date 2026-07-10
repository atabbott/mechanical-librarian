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
