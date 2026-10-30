# Review cases

A review case is the owner's unit of review for one target. It binds one exact
current [ProposalSet](proposal-set.md) to its report, target and file.
Preparation admits cases itself; the owner decides them in the workspace
([review experience](../product/review-experience.md)). Contributors run
admission through the `case-admit` and `case-inspect`
[workbench](../development/contributor-workbench.md) scenarios.

## Admission

Admission returns one typed result:

| Result | Meaning |
| --- | --- |
| `admitted` | A new case for an actionable ProposalSet. |
| `replayed` | The same exact inputs; the existing case is returned. |
| `superseded` | A new case replaces the active prior case. |
| `no_case` | Nothing actionable, and no active prior case. No empty case is made. |
| `retired` | Nothing actionable; the active prior case is retired. |
| `stale` | The report or media pointer lost currentness. |

A ProposalSet is actionable when any result is `propose_change`,
`acceptable_choice` or `unresolved_choice`.

Failures are redacted to `invalid`, `incompatible`, `currentness` (including
an unavailable currentness check) or `persistence`.

A `review-case-v2` case binds the exact ProposalSet, report, target and file
identity, and projects the exact `bibliographic-relationships-v1` document.

- Preserve selections are rechecked against the bound target field, so a
  changed value, or a change between missing, null and empty, changes case
  identity.
- Each destination's proposal ID binds the report, target and result set but
  not the relationship projection. Adding or removing a tuple changes the
  case identity without renaming an unchanged field proposal.
- A rejected tuple makes all three title, subtitle and series fields manual.

The admission transaction recomputes the policy evaluator's output from the
bound report. Any other ProposalSet is `incompatible` and publishes nothing.
Admission makes no decision, plan or write.

A newer report makes an earlier case historical without rewriting it.
Inspection never writes.
Earlier schemas are refused; rebuild from a clean state
([compatibility](../reference/compatibility.md)).

## Audiobook cases

An audiobook case is admitted, decided and read exactly as an ebook's. Its
decisions carry `application='apply'` and use the one guarded path
([ADR-0071](../architecture/decisions/0071-book-media-guarded-application.md)).

When only the book, not the recording, is supported, the case offers series,
title, description and genre choices, never subtitle
([review experience](../product/review-experience.md)). This
work-only scope gives no authority to Automatic, other fields, covers or
authored descriptions.

Audiobook Automatic needs the exact field, option and operation named by the
audiobook qualification record
([automation policy](../product/automation-policy.md)); other candidates stay
owner decisions. A fill record cannot authorize a replacement, because the
desired value is bound to the recorded pre-value and qualification digest.

A legacy `recorded_only` decision is read-only history on an audiobook case:
refused as a command, never planned, and stale on an ebook case
([ADR-0069](../architecture/decisions/0069-audiobook-review-cases-and-terminal-outcomes.md)).

## Field classification

Library, Attention and case detail share one closed field classification:

- `needs_attention` and `manual_fallback` are the only states that join
  Attention and that Next visits. An automatic abstention is `manual_fallback`.
- `automatic_recorded` stays inspectable and editable but is not owner work.
- `human_recorded` is a recorded owner decision.
- `unsupported` explains the missing capability without offering an empty
  value.
- `owner_choosable` is an editable settled fill that is neither queued nor
  counted, such as a tag or ASIN fill, or narrators outside "Authors and
  narrators: Ask me".

The list and both counts always agree.

## Preparation

For each item, preparation refreshes the [catalogue](catalogue.md), builds a
[metadata report](metadata-report.md), creates a ProposalSet, admits a case and
projects it into Library and Attention. It records automatic decisions only
where the active policy and safety posture qualify them
([automation policy](../product/automation-policy.md)). It adds no owner
decision, plan, write or deletion.

- **When it runs.** On first start, then
  `review.library_polling_interval_seconds` (default 900) after each
  successful scan. **Scan now** and **Retry** start a new cycle for one
  library. Repeated requests coalesce.
- **Concurrency.** Two fixed lanes let ready work pass a slow item. They
  serialize catalogue publication and share provider pacing.
- **Publication.** The report, ProposalSet, case (or no-case disposition) and
  projection publish together as one package, only after projection succeeds.
  During a refresh the last package stays browseable.
- **Absences.** Missing or ineligible items retire current lineages only if
  the whole scan completes and a confirming discovery agrees. The catalogue
  baseline builds one library at a time, so an unavailable library does not
  block healthy ones.
- **Drift.** An item that disappears, or a file that appears during
  extraction, publishes nothing and triggers bounded rediscovery. A failed
  rediscovery ends the scan.
- **Provider lanes.** A failed or no-match lane stays visible without erasing
  other evidence or delaying other work
  ([provider recovery](provider-recovery.md)).
- **Failures.** An item that fails before a package exists appears in Library
  only, with recovery guidance and no automatic retry. Fix the cause and use
  Retry; never edit SQLite.
- **Pause** stops new admissions without cancelling committed work.

A target connection failure (TLS, authentication, authorization or
availability) pauses only that connection's queued work; see
[catalogue](catalogue.md#connection-failures).

If preparation cannot commit its progress, the workspace shows **Preparation
stopped** and keeps published books available. Restart the service to resume;
refresh and Scan now cannot clear it
([observability](observability.md),
[ADR-0078](../architecture/decisions/0078-preparation-claim-and-crash-recovery.md)).

## Serving the workspace

`review-serve` serves the authenticated workspace and API:

```sh
mechanical-librarian review-serve --config config.json \
  --database reports.sqlite --catalogue catalogue.sqlite \
  --artifacts artifacts --host 127.0.0.1 --port 8443
```

`review.canonical_origin` must be the exact browser origin, including a
non-default port. Its scheme, with the TLS paths and trusted proxy list,
selects HTTP, direct HTTPS or HTTPS behind a trusted proxy
([configuration](../reference/configuration.md)). Transport, sign-in and request
rules are in [security](../architecture/security.md). The application never
issues or renews certificates; to rotate one, replace the files and restart.
`/health/live` and `/health/ready` are the only anonymous data
([observability](observability.md)).

## Decisions over the API

`/api/v1` serves case detail, evidence, history, Library, Attention, one
field-decision operation and authenticated OpenAPI. The
[OpenAPI document](../reference/review-openapi-v1.json) is the exact wire
contract.

A field decision names the displayed proposal ID and target fingerprint and
one outcome: `accept` (with the exact proposed value), `keep_current`,
`reject`, `owner_entered` (with the owner's value) or `withdraw`. It needs a
16–128 character `Idempotency-Key` and the session CSRF token. A
field-decision body is capped at 8 KiB and a sign-in body at 2 KiB; a larger
body is refused before parsing.

- A retry with the same key and payload returns the original decision.
  Conflicts and stale state fail with no partial decision or event.
- Errors: `404` for an absent case, `401` for an invalid session, `403` for a
  CSRF mismatch, `503` for a persistence outage.
- The response's `confirmation` is `confirmed` with the current case, or
  `unavailable` if readback failed after commit. Unavailable is still
  recorded; the browser offers a reload and never resubmits.

Library and Attention pages hold at most 50 works, with query-bound cursors.
Each response comes from one database snapshot, so a concurrent decision shows
throughout or not at all. A decision cannot create automatic authority, plan,
write or widen into a bulk operation.

## Policy activation

Policy drafts start from the active policy. Validate, create, read and preview
are deterministic, versioned operations, and the owner may activate only the
exact previewed draft ([automation policy](../product/automation-policy.md)).
The preview compares active and draft projections for up to 20 sampled cases;
its counts cover all current work. Preview grants no consent or effect
authority.

Activation is one transaction: it stores the resolved per-library policy,
retires every current unapplied old-policy case, fixes the transition total and
switches the active generation. Successors return through ordinary preparation
from retained evidence, with no new provider lookup, decision transfer or
write.

The transition status survives restart. `completed_with_failures` keeps typed
diagnostics and a retry. Retry restarts failed items whose evidence is still
current, shows items that moved to a newer report as reconciled, and leaves an
item with no preparation work failed. Work prepared after activation uses the
new policy at once; the transition total stays fixed. Acknowledging a finished
transition only dismisses its notice, and a new activation acknowledges any
undismissed one. Activations cannot overlap. A retry after a lost response
returns the original transition.

## Recovering title decisions

In Policy, expand Safety and recover the title suspension. Eligible completed
books re-evaluate their retained evidence; Library shows "Title recovery
requested", and Activity records a supported unchanged title as no change.
Recovery applies no metadata, and work waits until the last blocking control
(such as Pause) is cleared. Books with owner decisions, stale evidence, Ask or
Ignore policy, or work in progress are not requeued; a held book's new title
stays held until automatic handling resumes. No eligible books is a successful
recovery. A failed request keeps the suspension; inspect the state and retry.

## Covers

Detail shows the current cover and up to 12 report-bound alternatives. The
owner keeps the current cover, chooses an original, or takes no action;
reviewer observations and policy never choose
([review experience](../product/review-experience.md)). Setup and Settings
recommend GPT-6 Luna (`openai/gpt-6-luna`) as the cover reviewer.

- Preparing or retrying a comparison is explicit; reads never run the
  observer. Without a working reviewer, manual choices stay usable. Changing
  reviewer configuration makes older comparisons stale.
- Identity contradictions, unavailable artifacts and active searches are
  refused. Keep current stays available when every alternative is ineligible.
- A choice can be revised until a cover effect is claimed. Each revision
  checks the exact selection token; a stale response needs a reload.
- A choice is applied only through Activity consent and the guarded path,
  after every currentness, capability, backup and readback gate passes. There
  is no bulk or model-selected cover application.

The installed `cover-review` command (`show`, `history`, `keep`, `select`,
`no-action`) uses the same service as the API; `--help` lists its options.
Deciding commands check the selection ID that `show` prints. Retry an
uncertain command with its original idempotency key, expected selection,
artifact and flags.

## Activity from the command line

`review-activity` takes only `--config` and `--database` and prints the
Activity ledger read-only. It reads no service credential, never contacts
Audiobookshelf, and reports capability from persisted suspension evidence,
otherwise `not_ready`.
