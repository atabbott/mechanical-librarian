# Domain model

The domain layer (`src/mechanical_librarian/domain/`) holds the records the
product reasons about and the pure rules over them. It performs no I/O. This
page names each record, what it binds and what it may never do. Field-level
rules live in the operations guides it links to; the order the records are
produced in is in the [architecture overview](overview.md#data-flow).

The evidence and decision records follow these rules:

- **Closed vocabularies.** Every state, outcome, reason and kind is a fixed
  enumeration. An unknown value is refused, never mapped to a default.
- **Content-addressed identity.** A report, ProposalSet, case or decision ID
  is a digest of its canonical JSON (compact, key-sorted UTF-8). Replaying
  the same inputs gives the same bytes and the same ID, so there is no
  separate idempotency mechanism.
- **Immutable records, small mutable state.** Reports, ProposalSets, cases and
  decisions never change. Mutable state is limited to current pointers,
  settings and policy, preparation progress, field-effect operations and
  provider recovery.
- **Exact current schema.** Each record has one schema version. Records from
  an earlier development schema are refused, not interpreted
  ([ADR-0013](decisions/0013-pre-release-clean-schema.md)).
- **Evidence is not a decision, and a decision is not a write.** Only an owner
  decision or a qualified automatic decision can lead to a write, and only
  through a guarded field effect.

## Catalogue

A catalogue generation is one complete, bounded observation of every
configured Audiobookshelf book library: at most 10,000 `CatalogueEntry`
values, a monotonic generation number and a configuration fingerprint. Each
entry has the connection, library and item identity, display title and
authors, separate ebook and audio facets, and one classification: `none`,
`singular-supported` (one EPUB, or one mounted audio file set) or
`needs-attention`. Only a `singular-supported` entry carries an opaque file
identity.

A generation becomes current only when it is complete; the previous one stays
readable until then. Eligibility is an observation. It grants no authority to
read media or contact a provider. See [catalogue](../operations/catalogue.md).

## Metadata report

A `MetadataReport` is the immutable, neutral record of one item: one EPUB, or
one audiobook's set of 1 to 512 `m4b`, `m4a` or `mp3` files. It decides
nothing. Its contents are specified in
[metadata report](../operations/metadata-report.md).

**Target.** The report holds all 16 Audiobookshelf destinations (title,
subtitle, authors, narrators, series, genres, publishedYear, publishedDate,
publisher, description, isbn, asin, language, explicit, tags, cover). Each
has a value or one of `missing`, `null`, `empty`, `unloaded`, `malformed` or
`oversized`. One target fingerprint binds them all, with the opaque file
identity and content digest of the selected media.

**Local claims.** Facts read from the item itself are source-attributed
`LocalClaim` values of closed kinds (title, subtitle, creator, contributor,
series and sequence, ISBN, ASIN, publication date, other date events,
publisher, imprint, description, language, subject, genre, tag, duration and
others). Each keeps its source, bounded excerpt or provenance, and an
occurrence ordinal, so repeated and conflicting occurrences stay distinct and
in source order. Position never adds authority.

- Dates keep their exact lexical value, stated precision (`year`, `month`,
  `day` or `timestamp`) and event. Only an explicit statement about the
  selected edition is a `publication_date`; copyright, first publication,
  modification and other events stay `date_event` evidence.
- Contributor roles come from a closed role vocabulary. Production credits
  are `contextual_credit`, never a contributor.
- Each ISBN keeps its observed substring, checksum result and ebook, print,
  unknown or ambiguous applicability.

What each media pass admits is in
[opening ebook evidence](../product/opening-ebook-evidence.md) and
[opening audio evidence](../product/opening-audio-evidence.md).

**Provider observations.** Each enabled provider contributes one aggregate
observation built from its lanes (ISBN family, ASIN or work). A lane ends in
one closed status: `disabled`, `insufficient_input`, `candidates`,
`no_match`, `authentication`, `authorization`, `rate_limited`, `unavailable`,
`incomplete`, `malformed`, `oversized`, `candidate_limit_exceeded`,
`query_limit_exceeded` or `not_needed`. Candidates keep a provider-local work identity
separate from their edition identity. Their claims record `agreement`,
`disagreement`, `incompatible` or `not_comparable` against local claims, with
a named comparison method. Extra provider facts are limited to the closed
`ProviderEvidenceKind` list. No candidate is a winner, and lanes of one
provider that reach the same work count as one source, never as independent
corroboration.

An audiobook's `discovery_titles` (folder name, title tag) are
query text for one alternative work lane. They are never a local claim, a
field result or a proposal value.

Work identity is admitted only by the closed transformation table in
`domain/work_identity.py`. Adapters render its query text but cannot define
title equivalence. The admitting productions are listed in the
[glossary](../reference/glossary.md).

**ISBN relationships.** Every ISBN claim gets one derived relationship:
`accepted`, `associated`, `recording_associated`, `unrelated`, `invalid` or
`unresolved`, with a closed basis. Acceptance is per claim; replay recomputes
each relationship and rejects one that does not match
([rules](../product/opening-ebook-evidence.md)).

**Covers.** Cover outcomes come from the target, the media and cover-enabled
providers. Each keeps a closed designation (exact edition, related edition,
work, default or truncation), safe provenance and artifact facts. Identical
bytes share one artifact without losing distinct provenance.

**Currentness.** A separate current pointer per target and file names the
latest report. Moving it never changes an earlier report. Reopening by ID
returns that exact report.

## ProposalSet

A `ProposalSet` is the deterministic evaluation of one exact report under the
fixed built-in policy version and the resolved owner policy. It holds exactly
one result per destination, each with one outcome: `propose_change`,
`acceptable_choice`, `unresolved_choice`, `keep`, `defer`, `reject` or
`ineligible`. Alternatives carry a bounded value, scope, assessment, evidence
references and hashed correlation groups; they never copy excerpts, provider
payloads, URLs, paths or bytes. A separate projection lists the coherent
title, subtitle and series combinations.

The evaluator has no configuration, provider, network, persistence or write
authority. It has no score, ranking or provider priority, and it never infers
a deletion. Ambiguity becomes `unresolved_choice`, `defer` or `ineligible`,
never a silent pick. Outcomes and rules are in
[ProposalSet](../operations/proposal-set.md).

## Review case

A `ReviewCase` is the immutable, undecided unit of owner review for one exact
current target lineage. It binds the ProposalSet, report, target fingerprint
and file identity, and keeps all 16 results. `propose_change`,
`acceptable_choice` and `unresolved_choice` are actionable; the others are
context. A case records no decision and no write authority.

Each lineage change is an append-only transition: `admitted`, `superseded`,
`no_case`, `retired` or `tombstoned`. Admitting the same ProposalSet again is
reported as `replayed` and records nothing new. A newer report makes an
older case historical without rewriting it. An audiobook whose recording is
not admitted gets at most a work-only case (series, title, description and
genre choices, with no automatic authority); with no supported book evidence
either, it completes as evidence only. See
[review cases](../operations/review-case.md#audiobook-cases).

## Review preparation

Preparation is the only mutable workflow over these records. A scan covers
one configured connection and library. Its state is `queued`, `discovering`,
`processing`, `completed` or `completed_with_failures`, and it reports
checked, total, ready and blocked counts.

Each eligible item becomes one work row, bound to its source revision, file
identity and producer fingerprint. The row advances through the stages
`extraction`, `provider`, `proposal`, `admission` and `projection` to
`complete`. Its state is `queued`, `delayed`, `running`, `complete`, `failed`
or `stale`, and a complete row's outcome is `case`, `no_case` or
`evidence_only`. Library and Attention read one current review package
pointer per work.

When preparation runs, how it publishes packages and how it recovers after
a failure or restart are in
[preparation](../operations/review-case.md#preparation).

Rationale: [ADR-0031](decisions/0031-persisted-review-preparation.md),
[ADR-0078](decisions/0078-preparation-claim-and-crash-recovery.md).

## Decisions

### Owner decisions

A `HumanDecision` is one immutable owner fact for one case, proposal and
destination. Its outcome is `accept` (a retained alternative), `keep_current`,
`owner_entered` (typed text for title, subtitle or publisher, or an in-order
genre subset), `withdraw` (the field is undecided again) or `reject`, which
only a cover's "no action" records. Choosing an earlier value again is a new
decision. The latest decision for a case and destination counts unless it is
a withdrawal. Covers and descriptions have their own decision lineages and
cannot be withdrawn this way
([ADR-0085](decisions/0085-owner-entered-values-withdrawal-and-reselection.md)).

### Policy and automatic decisions

A `MetadataPolicyProfile` states the owner's intent per field: an option and
a destination (Automatic, Ask me or Ignore). It evaluates nothing and grants
nothing by itself. Activating a draft is atomic and retires the current
cohort, so older proposals become stale
([ADR-0034](decisions/0034-atomic-policy-generation-cutover.md)).

An `AutomaticDecision` is decision-only: it records a candidate for one
field, bound to the qualification that allowed that exact option
([ADR-0036](decisions/0036-qualified-decision-only-automatic-policy.md),
[ADR-0087](decisions/0087-operation-bound-qualification-authority.md)). When
the target already holds the chosen value, a `FieldEffectNoChange` fact
records that instead; it carries no value or authority. In the Preview
posture, deterministic shadow decisions and outcomes record what policy would
do without deciding. Which options are qualified is in
[automation policy](../product/automation-policy.md).

### Cover considerations

A cover consideration is an immutable, owner-facing record over one current
case. It keeps the report-bound cover packet, the deterministic identity
classification of each candidate, any advisory visual observation and the
owner's outcome (keep, select or no action). A retry creates a superseding
consideration. A `CoverAutomaticDecision` binds automatic cover authority to
one exact consideration
([ADR-0049](decisions/0049-owner-confirmed-cover-considerations.md),
[ADR-0059](decisions/0059-deterministic-cover-election.md)).

### Description advice

A description advice attempt sends deduplicated provider descriptions to the
selected reviewer and stores one authored candidate. An
`AuthoredDescriptionDecision` (human or automatic) accepts a stored candidate
by reference for guarded application. The model chooses nothing; see
[AI trust boundary](ai-boundary.md).

## Guarded field effects

A `FieldEffectPlan` turns one decision into one destination write. It binds
the target fingerprint, pre-value, desired value, target version, dependency
digests, authority, the owner's library-scoped `EffectConsent`, a capability
digest and an idempotency key. A title change may carry its subtitle as a
companion so both go in one request.

A `FieldEffectOperation` moves through a closed state machine:

| From | To |
| --- | --- |
| `planned` | `claimed`, `superseded`, `stale`, `abandoned` |
| `claimed` | `applying`, `reconciling`, `retryable`, `stale` |
| `applying` | `verified`, `reconciling`, `retryable`, `stale` |
| `reconciling` | `verified`, `retryable`, `stale` |
| `retryable` | `claimed`; `verified` when a pre-read shows the value already landed; `superseded` when authority or consent is withdrawn; `stale`; `abandoned` |
| `stale` | `superseded`, only for a plan staled by an unavailable connection |

`verified`, `superseded` and `abandoned` are terminal. `stale` is terminal
too, except that a plan staled by an unavailable connection becomes
`superseded` if its authority or consent is withdrawn. Such a plan is planned
again after a healthy probe, at most three times per decision. An operation
makes at most three attempts. Each transition is recorded as an append-only
event that Activity reads. The guards each claim rechecks are in
[security](security.md#consent-and-guarded-effects); rationale is in
[ADR-0037](decisions/0037-origin-neutral-guarded-field-effects.md) and
[ADR-0071](decisions/0071-book-media-guarded-application.md).

## Evidence batch

A `batch-manifest-v5` document freezes an ordered selection of catalogue
entries for the contributor workbench. It binds the catalogue generation, file
identities, a secret-free report-configuration fingerprint, the enabled
providers and call maxima, and its digest is its identity. A run revalidates
each entry and builds reports one at a time. Only the reports and cover
artifacts are durable; there is no progress, retry or resume state. See
[ebook evidence batches](../operations/ebook-evidence-batches.md).
