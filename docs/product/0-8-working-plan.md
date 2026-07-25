# 0.8.0 working plan

## Status

Milestone [0.8.0 — Independent multi-provider ebook evidence](https://github.com/atabbott/mechanical-librarian/milestone/9) is implemented by its single F09l slice tracker, [#210](https://github.com/atabbott/mechanical-librarian/issues/210), `independent Open Library ebook evidence`. This document records the delivered report-only contract; it does not authorize operation beyond that runtime.

## Outcome

One current Audiobookshelf EPUB with exactly one usable edition-applicable ISBN family produces or reopens one immutable report containing independent Hardcover and Open Library observations. Semantically equivalent Open Library claims match the existing Hardcover evidence surface, while an allowlisted finite rich-evidence set remains source-scoped. The checkpoint remains report-only: it adds no provider fusion, ranking, voting, proposal, review, apply, write, UI/API, third provider, generic cache/retry/scheduler/rate/cost/plugin framework, tag, or release.

## Open Library operating envelope

- Adapter selection remains a closed choice between built-in `hardcover` and `open_library` configuration; there is no registry or plugin surface.
- Disabled Open Library constructs no client and performs no egress. Enabled Open Library requires an explicit non-secret operational contact email used only in its identified User-Agent. The application must exclude that email from report JSON and IDs, manifests, audit/log/error output, fingerprints, and every persisted artifact; changing it neither supersedes reports nor invalidates a frozen batch.
- Use fixed HTTPS origins, an adapter-local fixed one-request-per-second pace shared by one foreground batch, single-attempt requests, at most two candidates, at most one Search plus two edition and two distinct work requests per report, and at most two covers per report.
- An exact candidate is only an edition document under Search work results whose normalized ISBN set contains the requested family, with valid `/books/OL…M` edition and `/works/OL…W` enclosing-work keys. Deduplicate and canonical-sort by edition key. Detecting a third exact candidate returns `candidate_limit_exceeded`, retains none, and makes no detail calls.
- Fetch admitted editions in canonical order and each distinct work once on first need. Zero exact candidates returns `no_match`. A required detail failure aborts the Open Library observation and retains no candidates: 429 is `rate_limited`; response overflow is `oversized`; invalid payload, shape, key, or required content is `malformed`; other transport and HTTP failures, including 404 and 5xx, are `unavailable`.
- Cover acquisition starts only after successful candidates and stays independently best-effort: cover failure never erases metadata.
- With Open Library enabled, only an explicit foreground selection of at most five items is accepted. `all_eligible` and larger selections fail before manifest creation, client construction, credential access, or egress. Larger existing batches remain available with Open Library disabled.

The fixed request shapes follow Open Library's official
[Search API](https://openlibrary.org/dev/docs/api/search),
[REST record API](https://openlibrary.org/dev/docs/restful_api),
[API usage guidelines](https://openlibrary.org/developers/api), and
[Covers API](https://openlibrary.org/dev/docs/api/covers). The installed
contract deliberately stays at the conservative one-request-per-second
guideline even when an identified client might be permitted a higher rate.

## Finite evidence and boundaries

Only the required exact Search, edition, and work payloads may supply rich evidence: exact work, edition, and author identities; revision and last-modified provenance; OCLC, LCCN, and Wikidata; subjects, people, places, and times; Dewey and LC classifications; physical format, pages, pagination, publication place, copyright date; contributor identity and role; edition-work relation and first-publication date; and bounded work and edition cover IDs.

The checkpoint does not traverse author records, enumerate editions, use fuzzy fallback or extra discovery, retain arbitrary links, raw payloads, unbounded text, or unknown identifiers. It does not map subjects into tags or genres, select a value, or change another provider observation.

## Delivered proof

Qualification proves closed adapter-specific configuration and no-egress
invalid/disabled paths; ISBN-family input gating before egress; deterministic
bounded parsing and claim parity; canonical provider ordering and failure
isolation; exact replay and supersession; cover safety and failure isolation;
truthful pre-egress batch maxima; and ADR-0013 absent-or-exact-current
`metadata-report-v6` behavior without migration, import, or conversion.
Installed-artifact, configuration, documentation, security, architecture, and
full required tests use only synthetic fixtures and mocked provider transport.

The single implementation pull request closes #210. Completing the milestone
does not create a tag, release, publication artifact, or production write
authority.
