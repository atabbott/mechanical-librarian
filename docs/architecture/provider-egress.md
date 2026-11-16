# Provider egress

This page is part of the [security authority](security.md): it states the
invariants every metadata-provider request, provider cover fetch and provider
recovery attempt keeps. The general rules for credentials and egress are in
[security: credentials and egress](security.md#credentials-and-egress).

## Metadata providers

A disabled provider builds no client and loses its library associations,
which enabling never restores.

- Every adapter dispatches through the bounded session in
  `adapters/providers/sdk.py` (request, retry, byte and deadline limits),
  with a 20-second share of the 60-second preparation deadline.
- Responses are read under per-response and aggregate byte ceilings and
  projected to closed facts. Overruns, quota and service failures are typed
  outcomes, never a no-match.
- A plan that cannot fit (more than eight ISBN families) is refused before
  a client exists; input with no ISBN and no complete title-and-author sends
  nothing.
- Retries cover only the statuses below and allow-listed transport causes,
  never TLS, proxy or decoding failures, and never inspect exception text.
- Reports keep a closed dispatch lineage, never queries, credentials or
  responses. [Dispatch](../reference/configuration.md#dispatch) sends
  audiobooks only to `AUDIOBOOK_DISPATCH_ADAPTERS` and ebooks only to
  `EBOOK_DISPATCH_ADAPTERS`.
- Clean observations' success bodies are kept owner-only for reuse, never
  request text or credentials; each is verified when read, not at open, and
  decoded and bounded as live
  ([ADR-0101](decisions/0101-reuse-stored-provider-answers.md)).
- Retained refused WORK answers serve only Book matching, never a live
  reader, support bundle or diagnostic, and fetch no cover
  ([refused work answers](../operations/metadata-report.md#refused-work-answers)).

Per provider, where *5xx* means 500, 502, 503 and 504:

- **Hardcover**: `POST` to the fixed GraphQL URL, with the bearer token on
  that request only. GraphQL `errors` are checked before `data` and yield
  nothing; their text is never kept. Retries 5xx, and 429 only when
  `Retry-After` or Hardcover's rate-limit reset fits the deadline. A WORK
  lane sends one constant books document whose only variables are two
  string lists: at most four case forms of the title and at most sixteen
  author names, each at most 512 characters. It matches with `_in` only and
  no pattern operator (`_like`, `_ilike`, `_regex`, `_iregex`, `_similar`).
  It asks for eleven books, so an eleventh, or more than five after folding
  a book into the `canonical_id` it names in the same answer, is a candidate
  limit. A request body over 16 KiB is never sent. An over-limit answer
  sends one retry with the first stored author alone, unless that is the
  author list just sent. An
  audiobook WORK lane that admits one or two books sends one constant
  audio-edition document per page, keyed by an integer book id: 100 rows a
  page, at most 2 pages per book, inside the same ceilings. A row naming
  another book refuses the whole fan-out
  ([ADR-0090](decisions/0090-hardcover-audio-edition-witness.md)). Its rows
  state each edition's image and language, which feed only the square
  audiobook covers of the pre-filter below. An ebook ISBN or WORK lane that
  accepts a book, when the library lists languages and the connection's
  covers are on, sends one constant edition-covers document per page, keyed
  by the integer book id and page offset: 100 rows a page, at most 2 pages
  per book and 2 books per observation. Only a book the work search marks as
  a compilation is skipped. Its rows feed cover descriptors only.
  A row naming another book, a malformed answer, a failure or the deadline
  stops the fan-out and records `edition_fetch_incomplete`, and a book or
  page past the bounds records `edition_limit`
  ([ADR-0056](decisions/0056-edition-overflow-work-witness.md)).
- **Open Library**: fixed-origin `GET`. The contact email goes only in the
  User-Agent to its fixed origins and is stored nowhere else. Retries 5xx,
  never 429.
- **Google Books**: fixed-origin `GET` with an optional API key that only the
  credential-bearing controller reads, sent in a header, never the URL.
  Retries 503 only.
- **Inventaire**: fixed-origin `GET`, no credential. Retries 5xx, and 429
  only with a bounded `Retry-After`; at most three attempts per request.
- **Audible catalog**: `GET` to ten hosts from a closed marketplace map, so no
  provider value becomes a host. An oversized page is refused, not truncated.
- **Audnexus** (`api.audnex.us`) and **AudioSilo** (under
  `meta.audiosilo.app/api/v1`): `GET` paths built only from identifiers of a
  closed shape, so they cannot leave the base path. AudioSilo's HTML error
  pages are never evidence.
- **iTunes Search**: `GET` to `itunes.apple.com/search`, with the query only
  in `term`. It states no narrator or runtime, so it never satisfies runtime
  corroboration. An ebook walks its title-and-author ladder. An audiobook asks
  one search, the first rung of its asserted lane, for a cover only, and only
  while iTunes' `cover_enabled` is true: no field value, backing, recording
  fact or recovery job comes from it (`provider_supplies_field_evidence`,
  ADR-0061 clause 9).

Audible, Audnexus, AudioSilo and iTunes send no credential and retry only
after `Retry-After` on 429 and 503, which Audible caps at one minute.

## Owner links

A work linked by its owner is read through that link before any search
([ADR-0104](decisions/0104-owner-identity-links.md)). The lookup is one more
lane under the same session, deadline, byte ceilings, retry rules and
response reuse as every other; it adds no origin, credential or host.

- **Hardcover**: one constant `BookById` document whose only variable is the
  integer book id the link stores.
- **Open Library**: `GET` of the stored work or edition key's JSON on the
  fixed origin, then counted detail reads: for a work, its edition listing
  (`/editions.json?limit=1`) and that one edition; for an edition, its work;
  and at most four authors. Only the linked record's own 404 reads as
  missing; a further read's failure is a failure.
- **Inventaire**: one entities `GET` naming the stored `wd:` or `inv:` URI.
- An edition link is asked as its ISBN and a recording link as its ASIN, on
  each source's existing identifier lane, built from the stored identifier
  rather than from any value a source returned.

A record that is absent reads as missing. A record that redirects or names
another record as its canonical one reads as merged, and the pointer is never
followed. A failed lookup is an ordinary provider outcome: the link stays and
[provider recovery](#provider-recovery) retries that lane alone, from the
report's own link facts. Lookups and their diagnostics are value-free like
every other provider request: no stored identifier, title or creator reaches
a log, event or diagnostic.

## Provider cover admission

Cover acquisition is report-only and needs the connection's `cover_enabled`.
At most four sources per named connection, eight for Hardcover, and twelve
overall are admitted. Admission fills each connection's exact, default, work,
related and listed covers in that order, in two passes, and records every
extra source as truncated (`provider_limit` or `gallery_limit`). A
descriptor is admitted only against a candidate the same provider observed in
the same observation: by a provider-local work identity or, when the provider
resolved no work, by one of that provider's own candidate editions. Two
unresolved placeholders never match, and a descriptor with no edition matches
nothing. A refused descriptor is dropped before any request is built.

Allowed cover origins:

- Audiobookshelf's raw cover route, with no credential, cookie or referrer.
- Hardcover, only on `assets.hardcover.app`, with no redirect.
- Inventaire, on constructed fixed-origin URLs.
- Open Library, on constructed fixed-origin URLs, with the closed redirect
  route below.
- Audible on `m.media-amazon.com`, and iTunes on `is1-ssl` to
  `is5-ssl.mzstatic.com`.

No other provider follows a redirect. Other hosts are `unsafe_remote`
before a socket opens. Remote covers' own transport bounds bytes, attempts
and deadline, pinned per hop; every answer must be public: IPv6 within
`2000::/3`, or an IPv4-mapped or `64:ff9b::/96` answer judged by its IPv4.
Embedded audiobook art is never acquired. Reports never keep the remote URL.

A Hardcover edition row becomes a listed-edition descriptor only after a
pre-filter on what the row states, before any request is built. Its stated
width and height must each be 1 to 8,192 pixels, with at most 16,777,216
pixels in all and a short side of at least 300. An ebook's cover must be
portrait, between 1.2 and 2.0 times as tall as it is wide, and an audiobook's
must be square within 5 percent. These acquisition bands are narrower than the
election bands of
[ADR-0093](decisions/0093-measured-cover-rules-and-current-cover-protection.md).
The row's stated language (`code2`, else `code3`) must be compatible with one
of the library's languages, and its URL must be on `assets.hardcover.app`.
Only a row whose cover is off that host, or whose cover or language is
malformed, is counted. No refused row is fetched. The stated size only filters
and orders rows, never reaching the report.

After retrieval, two listed-edition covers from the same provider whose
decoded measures are the same art by the measured cover rule
(`cover_rules.same_art`) collapse to the larger one, and the other is
recorded as truncated (`same_art`). A cover without measures never
collapses, nor do covers from different providers.

Open Library's route is a code constant on its two cover descriptors, never
configuration, and holds only for the constructed
`covers.openlibrary.org/b/id/<id>-L.jpg?default=false` URL. Each `Location`
header is matched exactly, before any join or decoding, by one anchored full
match: printable ASCII, lowercase `https://`, port absent or
443. Hop 1 must be `archive.org/download/<item>/<zip>/<file>` with no query,
fragment or `%`. Hop 2 must be `ia<N>.us.archive.org/view_archive.php` whose
`&`-split query holds exactly `archive` and `file`, bound byte for byte to hop
1. The file must be `<id>-L.jpg` or its ten-digit zero-padded form. At most
three requests, in fixed positions, share one attempt window; any other
redirect is `unsafe_remote` before a lookup. The `archive.org` hops send no
User-Agent, so the contact email stays on Open Library's origin. A retrieved Open Library cover records
only the `final_host` family: `covers.openlibrary.org`, `archive.org`
or `us.archive.org`.

Book matching (`review.work_identity`, #1355) changes which retrieved covers
the owner may choose, never what is fetched: a level reads only records the
report already retained. Saving it (CSRF-protected, revision-checked)
re-checks works from stored data, reusing earlier observer and advice output,
with no file, Audiobookshelf, provider, cover image or model request. A cover
matching only a level's title and author predicate keeps
`match_not_confirmed` and needs the owner's explicit unverified-match
confirmation ([ADR-0096](decisions/0096-owner-chosen-work-identity-strictness.md)).

## Provider recovery

[Provider recovery](../operations/provider-recovery.md) is background egress
under the same limits. Before building a client it rechecks the source
report, review package, target and media identity, provider binding and
configuration generation, and it runs outside write transactions. Missing or
contradictory quota evidence takes the conservative path. **Retry now** is a
Settings write that sends no request, and no view shows a key's value.
