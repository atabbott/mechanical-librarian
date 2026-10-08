# Audiobook recording-source comparison

**Recommendation: defer recording-authority adoption for both Hardcover audio
editions and AudiobookDB Releases.** The bounded October 2026 diagnostic for
[#1167](https://github.com/atabbott/mechanical-librarian/issues/1167) found useful
Hardcover work/runtime facts, but did not establish independent recording
evidence. AudiobookDB was not queried because its terms require separate written
permission for use with a product or service. Neither conclusion changes current
provider admission or closes
[#962](https://github.com/atabbott/mechanical-librarian/issues/962).

## What was compared

The stopped [#1086](https://github.com/atabbott/mechanical-librarian/issues/1086)
snapshot supplied 2,268 exact-current packages. Original report documents were
decoded through the current domain contract, with current report, proposal,
case and catalogue-generation bindings checked read-only. The 240 audio reports
represented 233 distinct source-file digests. These are files, not independent
works or an independently adjudicated gold corpus.

Selection was frozen before catalogue requests. Duplicate files used the
lexicographically first connection/library/item/report representative; a fixed
seed and source-file digest determined rank within each stratum. Admission took
precedence, then missing current-authority candidates, then a comparable runtime
difference greater than 90 and at most 600 seconds, then other comparable
contradictions. No selected failure was replaced.

| Baseline stratum | Report rows | Distinct files | Selected |
| --- | ---: | ---: | ---: |
| Missing recording candidates | 124 | 119 | 8 |
| Near runtime disagreement | 8 | 8 | 4 |
| Other comparable contradiction | 17 | 17 | 4 |
| Recording-admitted control | 91 | 89 | 4 |
| Incomparable remainder | 0 | 0 | 0 |
| Total | 240 | 233 | 20 |

The baseline inventory included ordinary and recording-lookup candidates from
the four current recording authorities. Hardcover work identity was checked
separately from runtime, identifiers and search rank. The existing 90-second
corroboration gate was unchanged; the 600-second selection band was diagnostic.

## Source contracts and access

Hardcover's public catalogue is a plausible source of audio-edition facts.
Its edition contract exposes audio duration in whole seconds, identifiers,
language, publisher, edition information, source and canonical/state fields.
Contributions distinguish Book authors from Edition narrators. These fields
describe storage precision and relationships; they do not guarantee accurate
measurement, complete credits or independent provenance.
([Pinned edition contract](https://github.com/hardcoverapp/hardcover-docs/blob/fe89c9e147ea7237b9e8d84451fd81dbd77aa63f/src/content/docs/api/GraphQL/Schemas/Editions.mdx),
[contribution contract](https://github.com/hardcoverapp/hardcover-docs/blob/fe89c9e147ea7237b9e8d84451fd81dbd77aa63f/src/content/docs/api/GraphQL/Schemas/Contributions.mdx),
[schema](https://github.com/hardcoverapp/hardcover-docs/blob/fe89c9e147ea7237b9e8d84451fd81dbd77aa63f/schema.graphql).)

Hardcover documents public-data use separately from user-owned data, requires
attribution for aggregates, restricts model training, and counts each top-level
GraphQL field toward quota. This diagnostic used Hardcover catalogue data only,
an existing private credential, and no images, user data or training. The API is
documented as beta, so the pinned contract is not a future compatibility promise.
([Access and limits](https://github.com/hardcoverapp/hardcover-docs/blob/fe89c9e147ea7237b9e8d84451fd81dbd77aa63f/src/content/docs/api/Getting-Started.mdx).)

AudiobookDB's Release model is recording-oriented: it separates a release from
its parent book, carries narrator relationships and a canonical millisecond
runtime, attaches ASINs to releases, and deliberately permits reused ISBNs.
Its guide describes Audnexus imports, so another database entry cannot be
presumed an independent source. These are documented capabilities, not observed
coverage. ([Release guide](https://audiobookdb.org/guidelines/04-releases),
[API reference](https://audiobookdb.org/docs/api).)

The [AudiobookDB terms](https://audiobookdb.org/terms), effective May 25, 2026,
require a separate written agreement for use connected with a product or
service. Consequently all 20 selected targets were access-blocked before any
AudiobookDB catalogue attempt. This is unavailable evidence, never zero matches.

## Bounded Hardcover observation

Ten targets began with retained, independently admitted work identifiers; two
began with local or asserted ASINs; eight began with the exact frozen title.
The fixed [search contract](https://github.com/hardcoverapp/hardcover-docs/blob/fe89c9e147ea7237b9e8d84451fd81dbd77aa63f/src/content/docs/api/guides/Searching.mdx)
used title/alternative-title fields without typo expansion. Only an empty
identifier result permitted a title-search fallback.

Queries admitted at most five work identifiers, retained at most 20 audio
editions, and read bounded contributions keyed by both entity type and ID.
Six search results, a twenty-first edition or a 401st contribution were overflow
witnesses; more than 16 contributions for one record was incomplete. Four
six-result searches ended unavailable without expanding their first five
results. No additional page, query repair or replacement target was attempted.
Other formats and unresolved canonical pointers were not followed.

The serial collector reserved each request before dispatch, enforced at least
one second between starts plus stricter observed limits, and disabled redirects
and retries. Dry HTTP controls proved that authentication/quota failures stop
all later targets. No such stop occurred in the live run. The frozen routes
allowed at most 52 physical calls within an 84-request planning envelope and
100-request hard cap, with at most four calls per target. Actual usage was
**43 physical requests and 59 top-level quota units**; schema/access allowance,
retries and AudiobookDB catalogue requests were all zero.

| Source | Selected | Attempted | Completed routes | Unavailable | Unattempted |
| --- | ---: | ---: | ---: | ---: | ---: |
| Hardcover | 20 | 20 | 15 | 5 | 0 |
| AudiobookDB | 20 | 0 | 0 | 20 access-blocked | 20 |

AudiobookDB's unavailable and unattempted columns describe the same 20 targets.
Hardcover's five unavailable routes were four search overflows and one
per-record contribution overflow. The 15 completed routes had these mutually
exclusive outcomes:

| Baseline stratum | Compatible work/runtime facts | Ambiguous or rejected | Bounded no-match |
| --- | ---: | ---: | ---: |
| Missing candidates | 0 | 3 | 1 |
| Near disagreement | 1 | 3 | 0 |
| Other contradiction | 1 | 2 | 0 |
| Admitted control | 3 | 0 | 1 |
| Total | 5 | 8 | 2 |

Bounded no-match means no audio edition in the returned scope, not catalogue
exhaustion. Ambiguous/rejected combines incomplete identity, explicit conflicts,
unresolved record state and facts insufficient for compatibility; it is not an
independent wrong-recording label.

## Marginal value and limits

The 15 completed routes returned 82 edition rows, including repeated editions
across targets. Of those rows, 58 stated a positive whole-second runtime and 47
passed the independent work comparison. Only two had usable edition-specific
narrator credits with resolved role objects. A textual narrator label with a
null role object remained unknown. Explicit language/recording-variant checks
found five conflicts; missing facts were not filled by inference.

Five targets had compatible work/runtime facts after explicit conflict and
record-state checks: three admitted controls and two unresolved non-controls.
The latter two supplied compatible runtime values absent from their retained
recording-candidate inventory. That is diagnostic marginal evidence, not two
newly admitted recordings. Their runtime agreement did not establish narrator
identity, measurement precision or independent lineage. Duplicate/canonical
records and shared identifiers were not independent votes; absent source lineage
remained unknown. **No independent recording was established.**

Defer Hardcover recording-authority adoption: the observed work/runtime value
justifies retaining this bounded finding, but the unresolved credits, lineage
and incomplete routes do not support promotion. Defer AudiobookDB comparison
until written product-use permission exists; its documented recording model
alone cannot establish marginal coverage. Reject the inference that either
source currently supplies an independent additional vote merely because it is
a different service. No runtime adapter, policy, schema, fixture or authority
changed. Any further collection or implementation needs its own bounded scope.

Private evidence retains the frozen selection, original bindings, request
accounting, response failures, rejected alternatives and per-target comparisons.
Snapshot digests were unchanged after collection. Public findings contain only
aggregates; they establish neither librarywide quality nor production benefit.
