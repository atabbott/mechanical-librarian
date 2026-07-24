# Current pre-release domain model

## Metadata evidence report

`MetadataReport` is one closed typed observation of one explicitly selected
EPUB. It contains:

- target connection, library, item, and exact current title;
- selected file index, opaque file identity, EPUB format, and content digest;
- stable source-attributed local field claims with bounded occurrence and, where
  applicable, rear-spine provenance;
- a local `candidate` or `no_candidate` status with bounded reasons;
- independent provider observations with stable input references and zero or
  more provider-identity-ordered candidates, each with an opaque provider-local
  work identity distinct from its edition identity; and
- one derived ISBN relationship for every local and provider ISBN claim.

The local field vocabulary is title/subtitle, creator/contributor and role,
series/sequence, ISBN, date, publisher/imprint, description, language, and
contextual subject evidence; typed field states remain evidence, not decisions.
Edition and rights are not local report-claim kinds: edition identity and
applicability may contextualize supported evidence, while rights observations
remain outside the report projection.

Local claims represent the closed field vocabulary. ISBN claims retain the
observed substring, canonical checksum-valid identifier when available, at
most 280 characters of directly supporting opening-matter context,
ebook/print/unknown/ambiguous applicability, and an explicit nonnumeric label
basis. Invalid, unlabeled, and conflicting-label identifiers remain distinct
facts; an ISBN-10 is not represented as an observed ISBN-13. Other bounded
local fields remain source-attributed evidence, never decisions, and preserve
their admitted source/DOM order and rear-spine provenance where present.
An explicit package primary-identifier designation is projected as ebook
applicability only when its value is a valid ISBN. A parser-inferred primary and
other package ISBNs remain independently provenanced and unlabelled.

ISBN relationships use the closed vocabulary `accepted`, `associated`,
`unrelated`, `invalid`, and `unresolved`. Their basis records explicit ebook
applicability, same-family package/opening agreement, provider identifier
agreement, provider-local work correlation, a different provider-local work,
invalidity, or insufficient context. ISBN-10 and ISBN-13 claims may compare as
one family without changing either retained observation. Repeated claims remain
separate relationship entries. Acceptance is claim-local: print, ambiguous,
conflicting, and inferred-primary occurrences do not inherit another
same-family occurrence's acceptance. Unlabelled package/opening agreement is
accepted only when every same-family occurrence is unlabelled and
non-conflicting. The derived projection contains claim
references and bounded provider identities, not another copy of observations
or excerpts, and exact replay rejects a relationship that does not recompute
from the report's claims and candidates.

Provider observations are `disabled`, `insufficient_input`, `candidates`,
`no_match`, `authentication`, `authorization`, `rate_limited`, `unavailable`,
or `malformed`. Candidate claims record agreement, disagreement, incompatible,
or not-comparable facts against stable local claim references. No outcome is a
decision or winner. Provider-local work identity may associate alternate ebook
or print ISBNs with an accepted identifier, but it cannot corroborate another
provider or make an unresolved claim accepted.

Claim references and the report ID are derived from canonical content. Exact
replay therefore preserves IDs and bytes without an idempotency subsystem.
Every admitted EPUB occurrence, including identical values, retains source/DOM
order through a bounded v2 occurrence ordinal in its claim identity.
The replay guarantee applies within the exact current schema; the domain model
does not interpret reports from prior or intermediate development schemas.

## Current book catalogue

One `CatalogueGeneration` is a bounded current observation across every
configured Audiobookshelf book library. It is identified by a monotonically
increasing generation and a deterministic configuration fingerprint and
contains at most 10,000 `CatalogueEntry` values.

Each entry has the complete connection/library/library-ID/item identity,
bounded title and author display values, independent ebook and audio facets,
and one ebook classification: none, singular supported EPUB, or
needs-attention. Only a singular supported EPUB may carry the opaque file
identity and mapped read-only path projection required by metadata reporting.
Catalogue eligibility is an observation, not authority to inspect media or
contact a provider.

Only a completely refreshed generation becomes current. The preceding current
generation remains readable until the atomic local pointer switch. A generation
is a bounded observation window rather than a perpetual Audiobookshelf
snapshot.

## Foreground evidence batch

One `batch-manifest-v1` document freezes an ordered set of exact catalogue
entries. It binds the current catalogue generation and fingerprint, selected
file identities, a secret-free report-configuration fingerprint, enabled
provider names, and bounded call maxima. Its content digest is its identity.

The manifest contains no user-facing metadata values, paths, credentials,
report bodies, progress, attempt, or retry state. `ForegroundBatchRun`
revalidates the current generation and exact entry before invoking the existing
metadata report operation serially. Item outcomes plus one final summary are
streamed to the operator; only the ordinary reports and cover artifacts are
durable.

The batch model has no queue, job, worker, scheduler, lease, resume, automatic
retry, parallelism, proposal, review, decision, or write concept. Manual
recovery is a fresh manifest and foreground run.
