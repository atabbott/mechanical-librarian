# ADR-0061: Audiobook provider posture

- Status: accepted
- Date: 2026-08-28
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Milestone 0.20.0 opens the audiobook evidence foundation (#749): a local
evidence harvest first, then provider discovery and enrichment. Today the
configuration schema admits exactly three built-in ebook provider adapters and
`metadata-report` rejects audiobook items before any parser, provider, or
publication activity, so the audiobook provider set is an open decision that
blocks the #749 provider slice and the #739 provider-SDK promotion.

Measured evidence, recorded on #925 and #749:

- Local identifiers cannot carry discovery alone. On the maintainer's
  reference library (236 items) an ASIN is present on some local surface for
  16.9% of items, 42.4% combined with target-service assertions — and those
  assertions disagree with embedded tags in roughly 17% of the overlap, so
  they corroborate rather than settle identity. Most items depend on provider
  discovery.
- The anonymous, credential-free Audible catalog endpoints have kept an
  unchanged response shape since February 2019, with no recorded gating,
  outage, removal, or enforcement against anonymous consumers in that period.
  The failure modes on record are bulk-load rate limiting with honored
  `Retry-After`, and omissive response drift that only breaks strict parsers.
- Coverage of the reference library's 103 known ASINs: Audnexus resolves
  103/103 — but only with marketplace fallback, because 27/103 resolve
  outside the US marketplace; the AudioSilo corpus covers 68/103; the
  AudiobookDB anonymous API covers 30/103. 17/103 known ASINs are
  ISBN-10-shaped rather than `B0…`-shaped and all are genuine audiobook
  identifiers.
- AudioSilo publishes its factual core as CC0 with a CC BY-SA community
  layer composed into the same downloadable artifact (409 MiB gzipped,
  1.6 GB raw, three-quarters chapter rows; an identity-core subset measures
  63.5 MiB gzipped). AudiobookDB publishes its data under a revocable
  personal-use license that excludes catalog redistribution and asks
  integrations to obtain a key through direct contact.

The keyless-first principle (fresh installs get evidence with zero keys) and
the recovery posture of ADR-0053 through ADR-0055 govern any admitted
provider. Audiobook evidence remains read-side only.

## Decision

1. **Admission.** The anonymous, credential-free Audible catalog API is
   admitted for read-side evidence — catalog search and product lookup — as
   the primary audiobook discovery and enrichment provider. Mechanical Librarian never requests, holds, or stores Amazon or
   Audible account credentials; keyed and device-registered access modes are
   not admitted.
2. **Layered order.** The admitted set for the first audiobook release, in
   order: the anonymous Audible catalog (primary discovery and enrichment);
   Audnexus (per-ASIN enrichment, including chapters, in place of the
   device-signed content endpoint); the AudioSilo hosted API (community
   corroboration and degraded mode); iTunes Search (corroboration only — it
   carries no narrator or duration fields). AudiobookDB is not admitted in
   this release; it becomes a candidate community tier only after maintainer
   contact establishes an integration key under its published integration
   terms, which also require crediting AudiobookDB. iTunes Search is asked
   an audiobook's cover alone (amended below, #1254 and #1779).
3. **Pure-API v1.** No provider dataset is bundled with or downloaded by the
   application in this release. The AudioSilo identity-core artifact is a
   deferred tier with exactly three activation triggers: anonymous catalog
   access becomes gated; a supported offline deployment demands it; or an
   accepted requirement to keep discovery queries on-box. If activated it is
   consumed as a versioned, checksummed dataset pinned like any dependency —
   not a provider cache — built from the CC0 core only, excluding the
   CC BY-SA community layer.
4. **ASIN identifier family.** ASIN is admitted as a first-class,
   marketplace-scoped identifier. Resolution implements marketplace fallback
   across the configured marketplace set, accepts ISBN-10-shaped ASINs
   without prefix filtering, and records the resolving marketplace as
   provenance.
5. **Failure posture.** Adapters parse tolerantly for optional evidence
   fields only: an absent optional field is absent evidence, never a
   failure. A response missing required identity or a required join key is a
   malformed response and fails closed into the typed failure vocabulary,
   preserving its recovery diagnostic — the fail-closed identity invariant
   in `docs/architecture/security.md` applies to these providers unchanged.
   Each provider receives one shared request-start pacer across foreground
   and recovery work; rate-limit responses honor `Retry-After`; failures
   classify into the ADR-0053 recovery vocabulary. Loss of the primary
   degrades discovery to the community tiers without a configuration change.
6. **Egress boundary.** The admitted providers inherit the existing
   provider-egress invariants: credential-free fixed-origin HTTPS, no
   ambient redirects, bounded bytes, requests, and deadlines, and
   hostile-response handling within the established parser constraints.
   `docs/architecture/security.md` gains their per-provider threat-model
   entries as required surface of the #749 provider slice, before any
   adapter egress activates.
7. **Setup surface.** Every admitted audiobook provider is keyless. Fresh
   installs get audiobook evidence with zero keys, and no key exists for
   these providers even as an optional upgrade.
8. **Schema entry.** The configuration schema's closed adapter set is
   unchanged by this record. The audiobook adapters enter the closed set
   through the #749 provider-slice schema revision under ADR-0013 — clean
   rebuild, no migration.
9. **Existing work evidence.** F46a (#1047) admits the existing Open Library
   linked edition/work/author witnesses and Hardcover bounded work projection
   for audiobook work facts only. Complete source associations and contributor
   bindings are required; contradictory or orphaned links remain refusals.
   Neither adapter enters the recording-authority registry. Audiobook original
   publication means a linked work's first publication, retaining year, month
   or day precision; recording and edition release dates do not vote.
   Hardcover states it from the admitted book's release date on an audiobook
   title-and-author lane only. That claim is offered while no recording is
   admitted and does not vote beside an admitted recording (#1642). This
   adds no provider, credential mode or network destination. The audiobook
   dispatch set is amended below (#1247). Hardcover also reads an admitted
   book's audio editions, as a witness to which recording the file is and
   never as recording authority or a second independent source
   ([ADR-0090](0090-hardcover-audio-edition-witness.md)).

## Consequences

### Positive

- Zero-setup audiobook evidence, with every known ASIN on the reference
  library resolved through the admitted providers (103/103, marketplace
  fallback included). Discovery for the items with no local identifier is
  not yet measured; it is a slice-2 qualification output.
- Provider adapters stay replaceable behind the existing chassis, and the
  recovery, pacing, and capability postures carry over unchanged.

### Negative

- The primary endpoints are undocumented and revocable without notice. The
  posture accepts that operationally — monitoring plus a defined degraded
  mode — rather than by holding credentials.
- Until slice 2 lands, most audiobook items (57.6% on the reference library)
  have no identity evidence beyond local surfaces.

### Neutral or deferred

- The local artifact tier and AudiobookDB are deferred with the named
  triggers above.
- Audiobook metadata writes and transcription remain governed by scope.md
  and the opening-audio evidence contract; this record grants neither.
- The chapter-duration corroboration tolerance model is measured during
  slice-2 qualification, not fixed here.

## Alternatives considered

### Community-first (AudioSilo and AudiobookDB primary, Audible via Audnexus only)

Rejected on measured coverage: 66% and 29% against a live 100%, with weaker
freshness for new releases.

### Keyed or device-registered Audible access

Rejected: it violates keyless-first as a default, would put operator Amazon
credentials inside Mechanical Librarian against the security posture, and no
evidence shows anonymous access is insufficient.

### Bundling or auto-downloading a provider dataset in v1

Rejected: the full artifact is 409 MiB gzipped / 1.6 GB raw and the
identity-core subset still adds download, refresh, and license machinery that
measured coverage does not justify while live endpoints answer completely.

### Founding a community database or adopting audio-fingerprint lookup services

Rejected during exploration: no audiobook coverage to inherit, and identity
here is carried by identifiers plus structural corroboration, not
fingerprint lookup.

## Validation

- Slice-2 qualification measures the discovery rate for identifier-less
  items, re-measures known-ASIN resolution, and classifies the four
  embedded-versus-asserted ASIN disagreements; known-ASIN resolution
  regressing below the measured 103/103 baseline reopens this record.
- Reopen triggers: anonymous catalog access gating, sustained community-tier
  collapse, or a supported offline-deployment requirement.
- The stability record and the coverage measurement are attached to #925 as
  its evidence comments.

## Audiobook dispatch set (#1247, #1254, #1779)

Audiobook preparation dispatches only to the connected providers named in
`AUDIOBOOK_DISPATCH_ADAPTERS` (`application/configuration.py`): the Audible
catalog, Audnexus, AudioSilo, Hardcover, iTunes Search (for a cover alone,
amended below) and Open Library. A library's `provider_connections` narrow
this set and can never widen it, and a binding with no authority is not
dispatched. Google Books and Inventaire are excluded on audiobooks: they get
no client construction, request,
observation, cover fetch, recovery job or retry, and an audiobook report gives
them no failure evidence, even an older observation a retry successor carried
forward. A recovery job already queued for one of them closes as `stale`,
which closes that job alone and records no failure evidence; a recovery row
whose remaining members all close as stale can still end through the runner's
existing success path. `config-v9`, the configuration schema and
`metadata-report-v53` are unchanged, so no clean rebuild is needed; a v53
report that holds an excluded observation stays valid.

**Clause 2 (#1254, superseded for covers by #1779 below).** iTunes Search
remains an admitted, configurable adapter, but it leaves the default audiobook
dispatch set, saving 181 dispatches over
#1086's retained items. #1254's measurement: 177 narrowed requests produced 0
new admissions; the 2 selected alternatives iTunes backed today were both also
backed by other providers, and under narrowing both overflow, leaving 0. #1246's
measurement: 234 requests, all to Audible, with iTunes at 0 while held for
#1254, and every alternative-title admission depended on Audible's alternative
lane. Where a library connects iTunes, it still searches ebook work lanes:
iTunes is in the ebook dispatch set below.

**Clause 9 (#1247).** The set is the six named above, iTunes for its cover
alone (#1779). Open Library and Hardcover stay, so F46a's and F54's work facts
keep their sources (#962).
Google Books and Inventaire leave: across #1086's retained audiobooks they
took 843 dispatches (31.6%), backed only 2 selected alternatives (both series,
from Inventaire) and no description or genre alternative, resolved none of the
297 unresolved choices, and left 189 recovery jobs queued; Inventaire was
unavailable on 80 of 238 items. Excluding iTunes as well (#1254) removed the
second audiobook cover authority until #1779 asked it again for its cover;
[ADR-0064](0064-audiobook-provider-evidence-vocabularies.md) records both.

**Clause 9 amendment (#1309).** Two members of the set send only what can carry
data. Open Library asks an audiobook work lane one rung, the first of
`work_query_ladder(..., audiobook=True)`, and sends no narrowing retry after a
candidate-limit overflow: on #1294's 237 audiobooks every Open Library
admission sat on the first rung and no narrowing retry admitted anything.
AudioSilo is dispatched last in each audiobook pass, and only when no Audible
catalog or Audnexus observation in that pass (the ASIN feed counts phase one's
Audible answer) admitted a recording by stated runtime. Otherwise it is
recorded as `not_needed`: its lanes are `not_dispatched`, it sends no request,
builds no client and queues no recovery job, and a failed Audible or Audnexus
never suppresses it. On #1294 this skips 302 AudioSilo dispatches on 119 items,
each already admitted elsewhere; the values only AudioSilo stated there were
other-edition ASINs and three series and three narrator statements whose
fields kept another alternative. Audnexus region misses get their own
`region_unavailable` diagnostic category instead of `unknown`. The new status
and category are `metadata-report-v67`, a clean rebuild under ADR-0013.

**Clause 9 amendment (#1468).** An audiobook item that holds an EPUB beside
its tracks (#1319) adds one lane from that companion EPUB's ISBN, when its
ISBN claims name exactly one ISBN family. Only Hardcover is asked it
(`COMPANION_ISBN_AUTHORITIES`), because the lane is useful only where the book
an ISBN names lists audio editions to compare with the file. Open Library is
in the audiobook set but lists no audio editions, so its answer could only
repeat its own work lane; adding it back is a one-line change to that set if
it ever lists them. Google Books and Inventaire were considered and are not
asked, because they are outside the audiobook set (clause 9). The Audible
family and iTunes are never asked it: an ebook ISBN names no recording. The
lane follows any product lane, comes before the searches, never suppresses a
search, and is added only where the query budget still has room. Hardcover
keeps a book from it at work shape only, never the ebook edition, and only
when the book is this work by the target's title and authors or lists an audio
edition counted against the file ([ADR-0090](0090-hardcover-audio-edition-witness.md)).
The companion's title is the first alternative search title, ahead of the
folder leaf and the title tag, and when the target names no complete work, the
companion's title and author creators are the search. Both are alternative
lanes, so, as for the folder leaf (#1255), only the Audible catalogue asks
them, and a product found on either is kept only as the recording the tight
band corroborates. The book providers are not asked the fallback: they would
admit a book, and offer its work-level values, on the EPUB's title and
creators alone, which the target never stated; Hardcover reaches the book
through the companion ISBN instead. Widening the fallback to them is a change
to `identifier_suppresses_discovery` and needs its own decision. None of it is a local
claim or a proposed value. This is `metadata-report-v69`, a clean rebuild
under ADR-0013.

**Clause 9 amendment (#1779).** iTunes Search returns to the set as a cover
source only (`COVER_ONLY_AUDIOBOOK_AUTHORITIES`). It asks one search per
audiobook: the asserted lane's first rung of `work_query_ladder(...,
audiobook=True)`, with no later rung and no narrowing retry, as Open Library
does under #1309. Its 600×600 artwork rendition and artwork host set are
unchanged. One domain predicate, `provider_supplies_field_evidence`, keeps its
observation out of every reader that turns a candidate into a field value,
backing, field result, recording fact or running time; the cover observer and
cover identity still read it, so its cover binds to its candidate. That cover
is `unknown` until ADR-0096's title and creator match admits it, and, like
every audiobook cover, is selectable only beside an admitted recording. Its
failures record no provider health and queue no recovery job
(`recovery_adapters`); a queued job closes as `stale`; a failed search yields
no cover. A bounded probe found an agreeing result for 50 of 51 audiobooks, 41
of them the Audible art at a higher resolution, which is why it is asked
again. It removes no Audible banner: every probed iTunes copy of a bannered
cover carried it too. It is searched for an audiobook only while its
`cover_enabled` is true, so the title and author are never sent for an answer
that would be discarded. `metadata-report-v73` marks the change, and the report
browse facts ignore its lanes on an audio report, so it never limits or matches
an audiobook (ADR-0013 clean rebuild). The configuration schema is unchanged.

**Clause 9 amendment (#1724).** Replay requires the companion ISBN lane
wherever the plan carried it: every Hardcover observation in a report whose
plan had budget room for the lane carries it, whatever its status, lane-less
observations included, and no observation carries it where the plan had no
room (a local ASIN overflow or an exhausted budget). One domain rule,
`planned_companion_isbn_lane`, decides room for both the plan and replay, so
they cannot drift. This is a stricter replay of `metadata-report-v69`, not a
new schema, and needs no clean rebuild: the lane and v69 landed together, and
every v69 producer (the adapter, a fault, a disabled binding and a retry
merge) records it.

## Ebook dispatch set (#1486)

Ebook preparation dispatches only to the connected providers named in
`EBOOK_DISPATCH_ADAPTERS`: Google Books, Hardcover, Inventaire, Open Library
and iTunes Search, which keeps its ebook title-and-author corroboration lane.
The rules are the audiobook set's: connections narrow it and never widen it, a
binding with no authority is not dispatched, and an excluded binding gets no
client construction, request, observation, recovery job, retry or failure
evidence. A recovery job already queued for one closes as `stale`.

Audible, Audnexus and AudioSilo are excluded: none has a documented ebook role.
On a 24-work synthetic-mix test library (18 EPUBs), Audnexus and AudioSilo
answered every EPUB `insufficient_input` without a request, and Audible's
title-and-author search caused 6 or 7 of the field items in attention while
making no title or author decision. The retained #744 ebook snapshot predates
the audiobook providers, so the set removes none of its 6,078 dispatches or
3,187 recovery jobs. No schema or report version changes, so no clean rebuild;
a retained report keeps its recorded lanes until its work is prepared again.

## Carried admission (#1316)

[ADR-0088](0088-carried-recording-admission.md) adds one ASIN origin: the
edition an earlier preparation of the same track set admitted, recorded as a
`prior_admission` claim. Like a target assertion it only obtains evidence: it
is never one of the item's identifier surfaces, never settles identity, and is
never offered as a local value.
