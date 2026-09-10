# ADR-0019: Close precision-aware publication-date comparison

- Status: accepted
- Date: 2026-07-30
- Owners: project maintainers
- Supersedes: ADR-0016 publication-date packet projection
- Superseded by: none

## Context

Publication observations already retained their original lexical value, event,
and year/month/day/timestamp precision, but comparison reduced them to generic
agreement or disagreement. That could not distinguish a day agreeing with a
coarse year from independently supported day precision, or preserve the shared
year when two finer dates conflicted. Provider publication text was also
non-selectable even when an exact ebook manifestation binding was available,
while unsafe dates needed to remain visible without gaining proposal authority.

## Decision

`closed-title-creator-publication-v4` is the exact current comparison policy.
Every publication-date link records one closed relation:

1. `exact`;
2. `month_compatible`;
3. `year_compatible`;
4. `finer_conflict`;
5. `event_incompatible`; or
6. `manifestation_incompatible`.

The link separately records its shared `year`, `month`, or `day` precision, or
null when there is no shared precision. This keeps a same-month day conflict
distinct from a same-year month conflict. Comparison is deterministic,
provider-order independent, and retains original `YYYY`, `YYYY-MM`, and
`YYYY-MM-DD` values without filling missing components.
Timestamp equality is evaluated at the stated lexical calendar-day granularity
used by the target projection, never by converting offsets to a UTC instant.

A provider manifestation date becomes selectable only when the complete
candidate contains a checksum-valid ISBN family exactly matching valid local
ISBN evidence. Both sides must be ebook or unknown applicability. A closed
print-format fact or explicit edition/work contradiction makes the
manifestation incompatible. Title, creator, provider rank, opaque work
identity, and query order cannot establish manifestation applicability.
Google Books publication text uses this same domain gate rather than an
adapter-specific exception.
An edition with a valid conflicting, duplicate, or multiple work relation
retains its exact canonical candidate binding and one closed contradiction
fact. Work-scoped claims, evidence, identity bindings, and work cover authority
are suppressed; edition-scoped evidence remains visible and non-voting.

Work-first, copyright, modification, unknown-event, and unsafe-manifestation
dates remain retained evidence. They carry explicit incompatible links into
the reviewer packet but never vote for `publishedYear` or `publishedDate`.
When a title-and-authors target metadata lookup has no local input claim, an
unsafe manifestation link records `local_reference: null` rather than
inventing a local claim reference. That unbound form is valid only for
normalized provider publication text reclassified as manifestation
publication with a `manifestation_incompatible` relation; malformed text and
all other event kinds cannot retain it. It remains non-comparable and
non-voting.
Shared year evidence may support `publishedYear`; a full-day `publishedDate`
requires two materially independent sources. When independently correlated
month-capable observations disagree only below the month, the proposal policy
projects their strongest shared `YYYY-MM` value as one month-precision
`publishedDate` candidate. Its references and correlations are the union of
the observations that support that precision. Finer source values and their
conflict remain in the report and reviewer citations. Different months project
only their shared year, and different years remain conflicting. Target state
does not create a correlation, and no missing component is selected or padded.

The exact-current report is `metadata-report-v22`, the proposal policy is
`fixed-conservative-v4`, the reviewer packet remains
`shadow-review-packet-v3`, and the request remains
`shadow-review-request-v4`.
ADR-0013 rejects all earlier pre-release database shapes without migration,
import, or compatibility parsing.

## Later evolution

The versions and two-source projection above record this decision's historical
boundary. The current evaluator requires local evidence plus two independent
provider authority correlations at the selected precision. It projects the
strongest supported year, month, or day as one canonical `publishedDate`;
`publishedYear` remains derived context rather than a second decision. The
precision, event, applicability, and no-invented-component constraints remain.
See [current publication sufficiency](../../operations/proposal-set.md#publication-candidate-sufficiency)
for the delivered rule and the #1003 retain-pending-evidence disposition. This
clarification changes no historical qualification or runtime policy.

## Consequences

### Positive

- Coarse agreement and finer conflict remain simultaneously explainable.
- Complementary exact-day observations can support a truthful shared month.
- Safe manifestation dates can contribute without provider-specific voting.
- Incompatible facts stay visible to reviewers and deterministic replay.

### Negative

- Exact ISBN-family applicability deliberately rejects many plausible records.
- A single full date cannot produce a selected day even when it supports a
  year.
- Audiobookshelf partial-date acceptance and exact read-back remain unqualified,
  so month candidates are report-only and not write-eligible.
- Existing pre-release report and proposal databases must be rebuilt cleanly.

### Neutral or deferred

- There is no work-first voting, provider ranking, fuzzy identity match,
  publication-event inference, date completion, migration, apply behavior, or
  production write.
