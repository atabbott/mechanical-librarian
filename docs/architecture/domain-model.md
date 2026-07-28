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

The local field vocabulary is title/subtitle, creator/contributor and controlled
role, contextual production credit, series/sequence, manifestation publication
date, other typed date event, publisher/imprint, description, language, and
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
Source-explicit target-work subtitle, publisher, and imprint claims use the
same closed kinds for front and rear indicia. Rear claims additionally retain
their bounded resource identity, spine ordinal, tail slot, marker, and exact
supporting excerpt. Publisher and imprint remain separate edition-scoped kinds.
Each complete nested or sibling label owns its exact occurrence span without a
synthetic combined value, while unmatched split labels in the same ancestor
remain assembled. Repeated or conflicting occurrences are retained without
source-position authority or automatic resolution.
An explicitly non-ISBN package scheme or URI owns its digit sequence, so a
checksum-valid substring within it is not an ISBN fact.
Local dates retain the exact lexical value and maximum stated `year`, `month`,
`day`, or `timestamp` precision. The closed event vocabulary keeps selected-
manifestation publication, work first publication, copyright, creation,
modification, conversion, and unknown events distinct. Only an explicit
selected-ebook/edition publication statement is a `publication_date`; the other
events remain `date_event` evidence for later policy. Signed offsets are
preserved and are never converted to UTC.
Provider dates use the same closed event and precision vocabulary. A provider
manifestation claim additionally retains its lexical and normalized values and
explicit linkage confidence. Work-first, copyright, modification, and
unresolved publication-text evidence remain distinct rich facts. Candidate
edition/work identity and sibling identifier/format facts provide linkage
context without converting provider assertions into truth.
Package contributor roles are projected to controlled author, translator,
illustrator, editor, foreword, designer, photographer, cartographer, adapter,
or structured-explicit `other` codes. Multiple declared roles remain one
canonical `+`-delimited role set on the same contributor occurrence. Production
roles such as exact `bkp` are retained as `contextual_credit`, never as a
bibliographic contributor or author.
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

Provider lane observations are `disabled`, `insufficient_input`, `candidates`,
`no_match`, `authentication`, `authorization`, `rate_limited`, `unavailable`,
`malformed`, `oversized`, `candidate_limit_exceeded`, or
`query_limit_exceeded`. One provider aggregate owns the configured authority,
canonical candidate set, and same-provider convergence across its child lanes.
Candidate claims record
agreement, disagreement, incompatible, or not-comparable facts against stable
local claim references. Open Library candidates may also retain only the closed
finite evidence kinds declared by `ProviderEvidenceKind`, never arbitrary
upstream properties or raw payloads. No outcome is a decision or winner.
Provider-local work identity may associate alternate ebook or print ISBNs with
an accepted identifier, but it cannot corroborate another provider or make an
unresolved claim accepted.

`ProviderQueryKind` records no request, one exact ISBN-family lane, one work
lane, or a multi-lane discovery aggregate. Every distinct valid ISBN family
has one opaque canonical lane retaining all triggering local references.
One work lane is admitted from an exact title and complete ordered
target-author list alongside any ISBN lanes. It retains work-scoped title,
authors, series/sequence,
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
`MetadataReport` under `fixed-conservative-v2`. It binds the report ID and
derived target fingerprint and fixed policy version. Its ordered result tuple
contains exactly one result for every
`TargetDestination`.

Results use the closed outcome vocabulary `propose_change`, `unresolved_choice`,
`keep`, `reject`, `defer`, or `ineligible`. A `propose_change` has one selected
alternative; an `unresolved_choice` retains bounded alternatives without a
selection; `defer`, `reject`, and `ineligible` have no alternative. `keep`
has one preservation alternative. An alternative contains one bounded typed
value, its work/edition/cover scope, target assessment, stable evidence
references, hashed correlation identities, and a closed event/target precision
for publication candidates. `reject` is reserved for future explicit policy
rejection; the fixed evaluator never infers deletion or emits it. `keep`
requires positive preservation evidence and never encodes a replacement. The
ProposalSet references observations owned by the report rather than copying
excerpts, provider payloads, cover provenance, URLs, paths, or bytes. Its ID
is derived from its canonical document rather than serialized in it.

## ReviewCase admission

An immutable undecided ReviewCase preserves all sixteen ProposalSet results for
one exact current target lineage. Only `propose_change` and
`unresolved_choice` are actionable; the remaining results are context, not
queue work. Cases do not record decisions or write authority.

The pure evaluator has no configuration, provider, network, persistence, or
write authority. Its fixed rules preserve ordered complete people lists,
atomic series name/sequence, linked publication date/year, accepted
edition-identifier applicability, and cover-gallery completeness. Untyped
contributor evidence cannot become a partial narrator list, and unresolved
ISBN applicability defers. Year-only edition evidence remains a year rather
than becoming a full date. Local work subtitles and exact-edition provider
subtitles retain their source-appropriate scope. A full date that refines a populated target year
needs two independent correlation groups. Populated
subjective fields are not merged or replaced. Multiple values, weak
correlation, duplicate connections to one provider authority, malformed/unloaded
target state, role ambiguity, or incomplete
cover evidence become unresolved choices, defer, or ineligible rather than
being silently resolved.
An exact controlled `Unknown` package-metadata creator sentinel remains a
versioned local claim in the report but is not an author candidate. Other
creator contexts and nonexact names remain ordinary evidence, and filtering a
sentinel does not reorder the remaining complete author list.
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

One `batch-manifest-v5` document freezes an ordered set of exact catalogue
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
