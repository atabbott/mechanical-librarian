# F09b Hardcover proof-of-concept contract

This is the frozen external and application contract for the live-compatible
F09b title proof of concept. It remains intentionally narrower than the
configured production provider planned for F09f.

## Upstream baseline

The authoritative source is the official `hardcoverapp/hardcover-docs`
repository at revision
`d8a324f9f397395cae8737d9c9f7dd5c74a64162`, specifically
`src/content/docs/api/Getting-Started.mdx`,
`src/content/docs/api/guides/Searching.mdx`, and `schema.graphql`. A bounded
live observation against the fixed endpoint on 2026-07-14 supplies the runtime
facts that the beta documentation does not specify accurately or completely.

At that revision and observation:

- the beta GraphQL endpoint is `https://api.hardcover.app/v1/graphql`;
- the guide says to use the token directly, but the live endpoint requires the
  provider-specific header value `Bearer <raw-token>`;
- a token is private, account-capable, and may act as its user;
- documented HTTP statuses are `200`, `401`, `403`, `404`, `429`, and `500`;
- the service permits 60 calls per minute, a 30-second maximum query timeout,
  and maximum GraphQL depth 3;
- `SearchOutput.results` is GraphQL `jsonb`, and the guide identifies Typesense
  and searchable book fields including `title`; and
- the live `results` value is the closed Typesense-shaped object below, not a
  list of title objects.

The authentication discrepancy and undocumented `jsonb` shape are recorded
explicitly rather than allowing a fake fixture to define the contract. The API
is beta and described as subject to change, so this narrow compatibility claim
is versioned and must be requalified against the exact installed artifact when
the contract changes.

## Fixed request

The adapter owns HTTPS `POST`, the endpoint, and this exact query:

```graphql
query HardcoverTitlePoc($title: String!) {
  search(query: $title, query_type: "Book", per_page: 1, page: 1, fields: "title", weights: "1", typos: "0") {
    error
    page
    per_page
    query
    query_type
    results
  }
}
```

The request body contains only the fixed `operationName`, `query`, and
`variables.title` fields. The query text fixes the operation and asks for one
title search result. The title variable is the bounded, repository-generated
F09b corroboration title and is sent only to find a matching public book title.
No target row, service credential, identifier, path, media, current metadata,
or operator input is sent.

The complete serialized request is at most 8,192 bytes. The title is at most
512 Unicode scalar values. The response is at most 65,536 bytes and the parser
accepts at most one hit, depth 16, 128 aggregate JSON nodes, 64 keys in one
object, 16 items in one list, and 512 scalar values in one string. The observed
live response was 2,402 bytes with depth 11, 107 nodes, 34 keys in its largest
object, three items in its largest list, and 158 scalar values in its longest
string. The operation has one attempt, no retry, no backoff, a five-second total
deadline, and concurrency one.

Transport uses normal certificate validation, no redirects, no environment
proxy (`trust_env=false`), no cookies, and no response compression. Before each
attempt every resolved address is rejected if it is loopback, link-local,
private, multicast, reserved, unspecified, or otherwise outside the approved
public destination class. The connection is made to the exact validated IP
while retaining `api.hardcover.app` for TLS SNI and certificate verification;
the connected peer address must equal that validated IP. Rebinding or an
unexpected peer fails closed. Authentication is attached only when the HTTPS
method, default port, exact path, empty query, empty fragment, host header, and
TLS SNI all match that request.

## Closed live response

The envelope is exactly `data.search`. Search contains exactly `error`, `page`,
`per_page`, `query`, `query_type`, and `results`. Error is null; page and
per-page are integer `1`; query exactly echoes the generated title; and type is
exactly `Book`.

`results` contains exactly `facet_counts`, `found`, `hits`, `out_of`, `page`,
`request_params`, `search_cutoff`, and `search_time_ms`. Facets are the observed
empty list. Counts, page, and time are integers from 0 through 2,147,483,647
rather than booleans; page is `1`; `found` does not exceed `out_of`; and at most
one hit is returned. A non-empty hit list requires a positive found count,
while the first page can be empty only when found is zero. Found and out-of may
exceed one when the fixed page contains one hit. Search-cutoff is a boolean.

Request parameters contain exactly `collection_name`, `first_q`, `per_page`,
and `q`. The collection is a non-empty bounded string, both query values exactly
echo the generated title, and per-page is integer `1`.

Each hit contains exactly `document`, `highlight`, `highlights`, `text_match`,
and `text_match_info`. The closed document validates, then discards, the live
title-search fields for activity and review counts, alternative titles, author
names, category, compilation, warnings, contribution types and contributions,
cover colour, description, empty featured-series object, genres, media-format
flags, upstream ID, image, ISBNs, lists, moods, pages, prompts, rating, release
date/year, series IDs/names, slug, subtitle, tags, title, and user/read counts.
Document and nested metadata integer fields reject booleans and are bounded
from 0 through 2,147,483,647; rating is a finite floating-point value in the
inclusive Hardcover scale 0.0 through 5.0; all strings and lists obey the global
bounds. Contributions
contain a closed author object and optional contribution string; author images
are the observed empty object. The image, title highlight, one-element
highlights list, integer text match, and text-match-info objects also use their
exact observed keys and types. The Typesense `text_match` score is separately
bounded from 0 through the signed 64-bit maximum
9,223,372,036,854,775,807; all other accepted integers retain the signed
32-bit ceiling above. Only `document.title` crosses the adapter boundary; rich
fields and upstream IDs are never retained.

The fixture in `tests/support/hardcover_fixtures.py` is irreversibly synthetic
but preserves the observed key/type/cardinality shape and the depth, node,
object, list, and string maxima. Duplicate, missing, null, wrong-type, unknown,
non-finite, over-bound, echo-mismatched, count-inconsistent, and multiple-hit
variants fail closed.

One valid bounded title is canonicalized into provider evidence. A count-
consistent empty first page maps to `provider_empty`; HTTP `404` maps to
`provider_no_match`; and a valid differing title maps through policy to
`local_provider_conflict`. HTTP `401`, `403`, `429`, or `500`, a non-null search
error, GraphQL `errors`, timeout, TLS/DNS/peer failure, oversize, malformed JSON,
shape drift, or an invalid title maps to `provider_failed`. Provider failure
never erases local F06 evidence or becomes negative bibliographic evidence.

## Secret and persistence boundary

Omitting `--secret-reference` constructs no adapter or client, resolves no
secret or DNS, performs no egress, exits `0`, and persists
`provider_disabled`. A malformed reference is syntax failure and exits `2`
before state, secret, or network access. A syntactically valid reference whose
file is missing, insecure, unreadable, or invalid performs no request, exits
`0`, and persists `provider_failed`.

The secret reference must be an absolute owner-only regular file, not a
symlink, with 1 through 2,048 bytes containing a raw printable non-whitespace
ASCII token. The file does not include the `Bearer` scheme; the Hardcover-
specific authentication adapter constructs it only for the fixed request. The
provider credential is isolated from target and reviewer credentials. Tokens,
secret paths, raw traffic, headers, request bodies, upstream identifiers, and
provider caches are never logged, reported, or persisted.

Canonical observations receive deterministic non-secret identities beginning
`hardcover-poc-`. Identical observations replay; different disabled, failed,
empty, no-match, disagreeing, and agreeing outcomes coexist without collision
or supersession. New observations carry durable provider manifest version
`hardcover-title-poc-v2`, which binds the Bearer authentication and closed live
response contract into every outcome, including zero-candidate outcomes.
Existing `hardcover-title-poc-v1` history remains readable without a schema
migration, but cannot replay as a v2 observation because the version changes
its canonical lineage and identity. All state remains `synthetic_preview` and
structurally derives `production_applicable=false`.

F09b changes no `config-v1` field and introduces no environment secret,
encrypted store, provider registry, general egress broker, cache, ranking,
live-target binding, multi-provider framework, AI, decision, or write surface.
Credential-free fake qualification and an exact installed-wheel real-endpoint
qualification are required. The live run uses the fixed generated query, a
dedicated non-production token, and disposable state, and retains only a
redacted aggregate result. F09f retains production configuration, persisted
provider work, retry/restart, freshness, reusable-observation, and later live-
target responsibilities.
