# 0.8.0 working plan

## Status

Milestone [0.8.0 — Independent multi-provider ebook evidence](https://github.com/atabbott/mechanical-librarian/milestone/9) is an open, planned report-only checkpoint. Its single Ready slice tracker is [#210](https://github.com/atabbott/mechanical-librarian/issues/210), `F09l: independent Open Library ebook evidence`. This document records that accepted delivery contract; it does not claim that Open Library support is implemented or authorize operation beyond the current report-only runtime.

## Outcome

One current Audiobookshelf EPUB with exactly one usable edition-applicable ISBN family will produce or reopen one immutable report containing independent Hardcover and Open Library observations. Semantically equivalent Open Library claims match the existing Hardcover evidence surface, while an allowlisted finite rich-evidence set remains source-scoped. The checkpoint remains report-only: it adds no provider fusion, ranking, voting, proposal, review, apply, write, UI/API, third provider, generic cache/retry/scheduler/rate/cost/plugin framework, tag, or release.

## Open Library operating envelope

- Adapter selection remains a closed choice between built-in `hardcover` and `open_library` configuration; there is no registry or plugin surface.
- Disabled Open Library constructs no client and performs no egress. Enabled Open Library requires an explicit non-secret operational contact email used only in its identified User-Agent. The application must exclude that email from report JSON and IDs, manifests, audit/log/error output, fingerprints, and every persisted artifact; changing it neither supersedes reports nor invalidates a frozen batch.
- Use fixed HTTPS origins, an adapter-local fixed one-request-per-second pace shared by one foreground batch, single-attempt requests, at most two candidates, at most one Search plus two edition and two distinct work requests per report, and at most two covers per report.
- An exact candidate is only an edition document under Search work results whose normalized ISBN set contains the requested family, with valid `/books/OL…M` edition and `/works/OL…W` enclosing-work keys. Deduplicate and canonical-sort by edition key. Detecting a third exact candidate returns `candidate_limit_exceeded`, retains none, and makes no detail calls.
- Fetch admitted editions in canonical order and each distinct work once on first need. Zero exact candidates returns `no_match`. A required detail failure aborts the Open Library observation and retains no candidates: 429 is `rate_limited`; response overflow is `oversized`; invalid payload, shape, key, or required content is `malformed`; other transport and HTTP failures, including 404 and 5xx, are `unavailable`.
- Cover acquisition starts only after successful candidates and stays independently best-effort: cover failure never erases metadata.
- With Open Library enabled, only an explicit foreground selection of at most five items is accepted. `all_eligible` and larger selections fail before manifest creation, client construction, credential access, or egress. Larger existing batches remain available with Open Library disabled.

## Finite evidence and boundaries

Only the required exact Search, edition, and work payloads may supply rich evidence: exact work, edition, and author identities; revision and last-modified provenance; OCLC, LCCN, and Wikidata; subjects, people, places, and times; Dewey and LC classifications; physical format, pages, pagination, publication place, copyright date; contributor identity and role; edition-work relation and first-publication date; and bounded work and edition cover IDs.

The checkpoint does not traverse author records, enumerate editions, use fuzzy fallback or extra discovery, retain arbitrary links, raw payloads, unbounded text, or unknown identifiers. It does not map subjects into tags or genres, select a value, or change another provider observation.

## Required proof

The future implementation must prove closed adapter-specific configuration and no-egress invalid/disabled paths; ISBN-family input gating before egress; deterministic bounded parsing and claim parity; byte-identical report output independent of provider configuration order; provider failure isolation; exact replay and supersession; cover safety and failure isolation; truthful pre-egress batch maxima; and, if persistence changes, ADR-0013 absent-or-exact-current schema behavior without migration, import, or conversion. Qualification includes installed-artifact, configuration, documentation, security, architecture, and full required tests using synthetic fixtures and mocked transport.

The single future implementation pull request closes #210. Completing the milestone does not create a tag, release, publication artifact, or production write authority.
