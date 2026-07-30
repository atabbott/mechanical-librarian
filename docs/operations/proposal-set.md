# ProposalSet operation

The 0.9 runtime can create one immutable ProposalSet from an exact current
metadata report:

```text
mechanical-librarian proposal-set-create \
  --config /run/secrets/mechanical-librarian.json \
  --database /data/reports.sqlite \
  --artifact-store /data/cover-artifacts \
  --report-id report_<64-lowercase-hex-characters>
```

Every option is required. The command reopens the exact report, revalidates its
current Audiobookshelf target projection, selected EPUB bytes, ABS cover, and
both blobs of every referenced private cover artifact, then evaluates the built-in
`fixed-conservative-v5` policy. It does not construct or contact a metadata or
cover provider.

An exact ProposalSet can be reopened offline:

```text
mechanical-librarian proposal-set-get \
  --database /data/reports.sqlite \
  --proposal-set-id proposal_set_<64-lowercase-hex-characters>
```

Success is exactly one canonical `proposal-set-v2` JSON document plus a
newline. Failure emits no document and one value-free diagnostic.

Only `fixed-conservative-v5` proposal documents and the exact current
`proposal_sets` table envelope are accepted. A database with the earlier
`fixed-conservative-v1`, `fixed-conservative-v2`, `fixed-conservative-v3`, or
`fixed-conservative-v4` table shape is incompatible and is rejected before
mutation; there is no proposal migration, import, conversion, or compatibility
parser.

## Fixed policy

Every ProposalSet is bound to the exact report ID, derived target fingerprint,
and fixed policy version. It
contains exactly one ordered result for title, subtitle, authors, narrators,
series, genres, publishedYear, publishedDate, publisher, description, isbn,
asin, language, explicit, tags, and cover. Each result is one of
`propose_change`, `acceptable_choice`, `unresolved_choice`, `keep`, `reject`,
`defer`, or `ineligible`.
The canonical document does not serialize its content-addressed ProposalSet ID;
the ID is derived from those canonical bytes and remains the exact-get input.

Alternatives retain only their bounded candidate value, work/edition/cover
scope, assessment, stable evidence references, hashed correlation groups, and,
for publication candidates, the closed event and target precision.
`publishedYear` alternatives always carry target semantics
`manifestation_publication` plus `year`; `publishedDate` alternatives carry
`manifestation_publication` plus their represented `month` or `day` precision;
every other destination carries neither. The exact source event and precision
remain separately available through the evidence reference in the immutable
report.
They do not copy report excerpts, raw provider evidence, responses, URLs,
paths, or image bytes. Provider access time remains in the exact immutable
report addressed by each evidence reference; proposal derivation performs no
provider access and does not refresh or manufacture that time.

`propose_change` carries exactly one selected safe candidate.
`acceptable_choice` carries at least two language or publisher candidates that
the closed field policy accepts and selects none; it is not a conflict.
`unresolved_choice` carries one or more bounded candidates but selects none
because a material contradiction or another unresolved rule remains. `defer`
carries no candidate because evidence or capability prevents a decision
surface. `keep` normally records one positive preservation alternative; for
language and publisher it may retain multiple alternatives when admitted
evidence supports the current value and every retained candidate is
policy-compatible. `reject` is a closed no-choice variant for a future
explicit policy rejection; the fixed evaluator has no deletion inference and
does not emit it. `ineligible` likewise has no choice. Proposal evaluation
grants no decision, apply, or write action.

The policy is deliberately conservative:

- populated values need positive preservation evidence for `keep`;
- canonical ISO 639-1/639-2 aliases identify one base language. A valid
  language-only observation can support a current, more specific script or
  region when no observation contradicts it. Different base languages,
  specified scripts, or specified regions remain unresolved, as do malformed
  or noncanonical tags;
- valid edition-bound publisher and imprint observations may each be an
  acceptable publisher-field outcome. Provider observations must also pass
  the existing exact selected-EPUB ISBN-family binding and closed format/work
  contradiction checks. Their original values and references stay in separate
  alternatives: the policy asserts no legal equality, hierarchy, parent,
  imprint, brand, or fuzzy company match. Associated-manifestation,
  work-scoped, unrelated, unresolved-scope, malformed, and target-only
  statements grant no publisher authority;
- populated replacement candidates normally need one unique value from at
  least two materially independent correlation groups. Otherwise, bounded
  choices remain `unresolved_choice`. Connections to the same upstream adapter
  are one group; Hardcover and Open Library are distinct groups;
- ordered author and narrator lists, series name/sequence, and date/year remain
  complete and linked rather than being edited piecemeal; untyped contributor
  evidence cannot become a partial narrator list;
- an exact case/width/whitespace-normalized `Unknown` creator from package
  metadata is a controlled placeholder, not an author candidate. The source claim,
  provenance, report identity, and sibling results remain visible and
  unchanged; real creators in the same report keep their source order, while
  `Anonymous`, nonexact names, provider creators, and explicit opening-matter
  creators remain eligible under the ordinary rules;
- selecting a full-day `publishedDate` always requires two materially
  independent correlation groups, whether or not a year is populated;
- two independent month-capable observations that agree through `YYYY-MM`
  produce one month candidate. Differing exact days in that month are
  complementary month evidence, not competing month candidates. The candidate
  carries the union of their evidence references and correlation groups;
- work first-publication, copyright, modification, and unknown-event evidence
  never vote for manifestation date or year fields;
- direct year-only edition evidence can fill `publishedYear` but is not
  promoted into a full `publishedDate`;
- one month observation alone derives only its stated year. A month candidate
  requires independent month-capable support; day evidence may become a
  `publishedDate` candidate, while a signed-offset timestamp may reduce to that
  exact stated day without offset conversion. Source precision remains in the
  cited report fact;
- a timestamp without `Z` or an exact signed `HH:MM` offset remains visible in
  the report but supplies neither a year nor day vote; this includes naive and
  parser-admitted offset-second forms;
- a day plus an agreeing year supports the shared year twice but the day only
  once. A day plus an independently agreeing month supports that month.
  Different months preserve only their shared year; different years remain
  conflicting. No rule fills or invents a missing month or day;
- provider publication text whose event or manifestation linkage is unresolved
  remains visible evidence and never becomes a selectable candidate;
- local work subtitles and exact-edition provider subtitles remain in their
  respective evidence scopes;
- populated description, genres, and tags are never merged, replaced, or
  cleaned; exact evidence may preserve them and candidates may fill only an
  empty field;
- ISBNs must have an `accepted` report relationship proving validated,
  edition-applicable evidence; unresolved applicability defers, and ISBN-13
  and ASIN are never manufactured;
- explicit changes require direct evidence, so absence supports neither
  `false` nor `keep`; and
- an existing verified ABS cover is kept. An absent target gets a selected
  cover only when the complete bounded gallery contains exactly one distinct
  safe artifact; complete competing bounded galleries are
  `unresolved_choice`, while incomplete galleries defer with no choices.

There are no scores, provider priority, configured policy, fuzzy matching, AI,
review admission, decision, or apply behavior.

## Currentness and persistence

External target, media, cover, and artifact validation finishes before the
ProposalSet insertion transaction. The transaction then immediately rechecks
that the immutable report document has the expected digest and that
`current_metadata_reports` still points to that exact report for its complete
scope. Drift inserts nothing. Audiobookshelf or media may change after the last
external read; perpetual cross-system atomicity is not claimed, and the future
review-admission stage must validate freshness again.

`proposal_sets` is append-only and has no current pointer, lifecycle, retry,
job, or normalized evidence tables. Identical exact inputs converge on the
same ID and bytes. The report database accepts only an absent database or its
exact current report-plus-ProposalSet schema; earlier development schemas are
rejected without migration, import, or conversion.
