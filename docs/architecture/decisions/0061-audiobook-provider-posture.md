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
   terms, which also require crediting AudiobookDB.
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
