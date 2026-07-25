# Current pre-release domain model

## Metadata evidence report

`MetadataReport` is one closed typed observation of one explicitly selected
EPUB. It contains:

- target connection, library, item, and the exact ordered state of all 16
  Audiobookshelf destinations, with one fingerprint binding them all;
- selected file index, opaque file identity, EPUB format, and content digest;
- stable source-attributed local field claims with bounded occurrence and, where
  applicable, rear-spine provenance;
- a local `candidate` or `no_candidate` status with bounded reasons;
- independent provider observations with stable input references and zero or
  more provider-identity-ordered candidates, each with an opaque provider-local
  work identity distinct from its edition identity and an optional closed,
  canonical, bounded tuple of provider-specific rich evidence;
- bounded ABS, EPUB, and provider cover outcomes with safe typed provenance,
  artifact facts, and explicit truncation; and
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
Publication package dates with an explicit standards-valid signed UTC offset
retain their original lexical form and declared publication event. They are not
converted to UTC or reclassified from creation, modification, copyright, or an
unknown event.
Package contributor observations retain their raw declared roles, while an
exact normalized `bkp` book-producer role is omitted from the bibliographic
contributor projection. Other declared roles are preserved unchanged.
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
`malformed`, `oversized`, or `candidate_limit_exceeded`. Candidate claims record
agreement, disagreement, incompatible, or not-comparable facts against stable
local claim references. Open Library candidates may also retain only the closed
finite evidence kinds declared by `ProviderEvidenceKind`, never arbitrary
upstream properties or raw payloads. No outcome is a decision or winner.
Provider-local work identity may associate alternate ebook or print ISBNs with
an accepted identifier, but it cannot corroborate another provider or make an
unresolved claim accepted.

`ProviderQueryKind` records whether no request, exact ISBN-family observation,
or work fallback produced the provider observation. Work fallback is admitted
only from an exact title and complete ordered target-author list and only when
no usable ISBN exists. It retains work-scoped title, authors, series/sequence,
description, typed taxonomy, first publication, and cover evidence; its
application filter removes edition claims and fails closed for competing or
translated/adapted/abridged works.

Provider covers use exact-edition, related-edition, work, default, or
truncation designation. Provenance contains only the configured provider,
opaque work/edition identities, and an allowlist of safe ISBN, language,
country/publication-place, publisher, date, and format context. The report
admits at most four source outcomes per provider and eight provider outcomes
overall. Content-addressed artifact reuse does not discard distinct source
provenance.

Claim references and the report ID are derived from canonical content. Exact
replay therefore preserves IDs and bytes without an idempotency subsystem.
Every admitted EPUB occurrence, including identical values, retains source/DOM
order through a bounded v2 occurrence ordinal in its claim identity.
The replay guarantee applies within the exact current schema; the domain model
does not interpret reports from prior or intermediate development schemas.

## Explainable ProposalSet

`ProposalSet` is the deterministic, content-addressed evaluation of one exact
`MetadataReport` under `fixed-conservative-v1`. It binds the report ID and
document digest, target fingerprint, policy, contract, and result-template
versions. Its ordered result tuple contains exactly one result for every
`TargetDestination`.

Results use the closed outcome vocabulary `needs_review`, `keep`, `reject`,
`defer`, or `ineligible`. An alternative contains one bounded typed value, its
work/edition/cover scope, target assessment, stable evidence references, and
hashed correlation identities. Only `needs_review` identifies a selected
alternative. `keep` requires positive preservation evidence and never encodes a
replacement. The ProposalSet references observations owned by the report
rather than copying excerpts, provider payloads, cover provenance, URLs, paths,
or bytes.

The pure evaluator has no configuration, provider, network, persistence, or
write authority. Its fixed rules preserve ordered complete people lists,
atomic series name/sequence, linked publication date/year, edition identifier
applicability, and cover-gallery completeness. A full date that refines a
populated target year needs two independent correlation groups. Populated
subjective fields are not merged or replaced. Multiple values, weak
correlation, malformed/unloaded target state, role ambiguity, or incomplete
cover evidence defer or become ineligible rather than being silently resolved.
There is no score, provider priority, ranking, configurable policy, review, or
decision concept.

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

One `batch-manifest-v2` document freezes an ordered set of exact catalogue
entries. It binds the current catalogue generation and fingerprint, selected
file identities, a secret-free report-configuration fingerprint, enabled
provider names and adapter discriminators, and bounded call maxima. Its content
digest is its identity.

The manifest contains no user-facing metadata values, paths, credentials,
report bodies, progress, attempt, or retry state. `ForegroundBatchRun`
revalidates the current generation and exact entry before invoking the existing
metadata report operation serially. Item outcomes plus one final summary are
streamed to the operator; only the ordinary reports and cover artifacts are
durable.

If an enabled Open Library provider participates, the manifest may contain only
an explicit selection of at most five entries. The shared runtime pacer spans
the whole foreground run and provides request-start spacing without becoming
durable batch state.

The batch model has no queue, job, worker, scheduler, lease, resume, automatic
retry, parallelism, proposal, review, decision, or write concept. Manual
recovery is a fresh manifest and foreground run.
