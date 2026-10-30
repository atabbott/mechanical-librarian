# Audiobook recording-source comparison

**Recommendation: defer adopting Hardcover audio editions or AudiobookDB
Releases as recording authorities.** A bounded sample showed that Hardcover can
supply compatible work and runtime facts, but no independent recording evidence.
AudiobookDB was not queried, because its terms require written permission for
use with a product or service. Hardcover remains a work-fact source on
audiobooks
([ADR-0061](../architecture/decisions/0061-audiobook-provider-posture.md));
filling recording gaps from its audio editions is open in
[#962](https://github.com/atabbott/mechanical-librarian/issues/962).

## Method

Twenty audiobook targets were drawn, with a fixed seed, from a library snapshot.
The sample favoured hard cases: items missing recording candidates, items whose
runtime disagreed with a candidate by 90 to 600 seconds, items with other
contradictions, and a few already-admitted controls. The existing 90-second
runtime corroboration gate was not changed.

Each target was looked up in Hardcover by its admitted work identifier or ASIN
where it had one, otherwise by exact title. A title search followed an
identifier lookup only when that lookup found nothing. Queries were capped at
five work identifiers, 20 audio editions and 16 contributions per record;
anything larger counted as an incomplete route, never as a partial answer.
Requests were serial, at least one second apart, and never retried.

## Source contracts

**Hardcover.** Its edition schema exposes audio duration in whole seconds,
identifiers, language, publisher, edition and state fields. Contributions
separate book authors from edition narrators
([edition contract](https://github.com/hardcoverapp/hardcover-docs/blob/fe89c9e147ea7237b9e8d84451fd81dbd77aa63f/src/content/docs/api/GraphQL/Schemas/Editions.mdx),
[contribution contract](https://github.com/hardcoverapp/hardcover-docs/blob/fe89c9e147ea7237b9e8d84451fd81dbd77aa63f/src/content/docs/api/GraphQL/Schemas/Contributions.mdx)).
These fields say how data is stored, not that it is accurate, complete or
independently sourced. Hardcover requires attribution for aggregate use,
restricts model training, counts each top-level GraphQL field toward quota, and
describes its API as beta
([access and limits](https://github.com/hardcoverapp/hardcover-docs/blob/fe89c9e147ea7237b9e8d84451fd81dbd77aa63f/src/content/docs/api/Getting-Started.mdx)).

**AudiobookDB.** Its Release model is built around recordings: a release is
separate from its book, carries narrators and a millisecond runtime, and holds
ASINs; ISBNs may be reused. It imports from Audnexus, so its entries cannot be
assumed independent of a source Mechanical Librarian already uses
([release guide](https://audiobookdb.org/guidelines/04-releases),
[API reference](https://audiobookdb.org/docs/api)). Its
[terms](https://audiobookdb.org/terms) require a separate written agreement for
product use, so all 20 targets were blocked before any request. That is
unavailable evidence, not zero matches.

## Hardcover findings

Fifteen of the 20 routes completed; five ended incomplete on search or
contribution overflow.

| Sample group | Compatible work and runtime | Ambiguous or rejected | No audio edition returned |
| --- | ---: | ---: | ---: |
| Missing candidates | 0 | 3 | 1 |
| Near runtime disagreement | 1 | 3 | 0 |
| Other contradiction | 1 | 2 | 0 |
| Admitted controls | 3 | 0 | 1 |
| Total | 5 | 8 | 2 |

"Ambiguous or rejected" covers incomplete identity, explicit conflicts,
unresolved record state and too few facts. It is not a wrong-recording label.
"No audio edition returned" applies to the bounded scope only, not the whole
catalogue.

Most returned edition rows stated a runtime and passed the work comparison, but
only two had usable edition-level narrator credits. A narrator name without a
resolved role stayed unknown, and missing facts were not inferred. For two
non-control targets, Hardcover returned a compatible runtime that their existing
candidates lacked. That is diagnostic evidence, not an admitted recording:
runtime agreement did not establish narrator identity, measurement precision or
independent lineage, and duplicate records or shared identifiers were not
counted as separate votes.

## Conclusion

- **Hardcover:** useful work and runtime facts, but unresolved narrator credits,
  unknown lineage and incomplete routes do not support recording authority.
- **AudiobookDB:** revisit only once written product-use permission exists; its
  recording model alone does not show added coverage.
- A different service is not, by itself, an independent vote.

The sample is small and was chosen for difficulty, so it says nothing about
library-wide quality. Further collection or implementation needs its own
bounded scope.
