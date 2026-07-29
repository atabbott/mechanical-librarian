# ADR-0019: Close precision-aware publication-date comparison

- Status: accepted
- Date: 2026-07-29
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
`manifestation_incompatible` and remains non-comparable and non-voting.
Shared year evidence may support `publishedYear`; a full-day
`publishedDate` requires two materially independent sources.

The exact-current report advances to `metadata-report-v22`, the proposal policy
to `fixed-conservative-v3`, the reviewer packet to
`shadow-review-packet-v3`, and the request to `shadow-review-request-v4`.
ADR-0013 rejects all earlier pre-release database shapes without migration,
import, or compatibility parsing.

## Consequences

### Positive

- Coarse agreement and finer conflict remain simultaneously explainable.
- Safe manifestation dates can contribute without provider-specific voting.
- Incompatible facts stay visible to reviewers and deterministic replay.

### Negative

- Exact ISBN-family applicability deliberately rejects many plausible records.
- A single full date cannot produce a selected day even when it supports a
  year.
- Existing pre-release report and proposal databases must be rebuilt cleanly.

### Neutral or deferred

- There is no work-first voting, provider ranking, fuzzy identity match,
  publication-event inference, date completion, migration, apply behavior, or
  production write.
