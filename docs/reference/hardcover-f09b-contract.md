# F09b Hardcover proof-of-concept contract

This is the frozen external and application contract for the F09b title proof
of concept. It is intentionally narrower than a supported provider adapter.

## Upstream baseline

The authoritative source is the official `hardcoverapp/hardcover-docs`
repository at revision
`d8a324f9f397395cae8737d9c9f7dd5c74a64162`, specifically
`src/content/docs/api/Getting-Started.mdx`,
`src/content/docs/api/guides/Searching.mdx`, and `schema.graphql`.

At that revision:

- the beta GraphQL endpoint is `https://api.hardcover.app/v1/graphql`;
- the `authorization` header value is the token directly, without a scheme;
- a token is private, account-capable, and may act as its user;
- documented HTTP statuses are `200`, `401`, `403`, `404`, `429`, and `500`;
- the service permits 60 calls per minute, a 30-second maximum query timeout,
  and maximum GraphQL depth 3; and
- `SearchOutput.results` is GraphQL `jsonb`. The guide lists searchable book
  fields, including `title`, but does not specify a versioned inner JSON shape.

The API is beta and described as subject to change. F09b therefore makes no
supported-live-compatibility claim.

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
title search result. The title
variable is the bounded, repository-generated F09b corroboration title; it is
sent only to find a matching public book title. No target row, service
credential, identifier, path, media, current metadata, or operator input is
sent.

The complete serialized request is at most 8,192 bytes. The title is at most
512 Unicode scalar values. The response is at most 65,536 bytes and the parser
accepts at most one result, depth 8, and 64 aggregate object/array nodes. The
operation has one attempt, no retry, no backoff, a five-second total deadline,
and concurrency one.

Transport uses normal certificate validation, no redirects, no environment
proxy (`trust_env=false`), no cookies, and no response compression. Before each
attempt every resolved address is rejected if it is loopback, link-local,
private, multicast, reserved, unspecified, or otherwise outside the approved
public destination class. The connection is made to the exact validated IP
while retaining `api.hardcover.app` for TLS SNI and certificate verification;
the connected peer address must equal that validated IP. Rebinding or an
unexpected peer fails closed.

## Closed proof-of-concept response

The credential-free fake exercises the same parser with exactly this successful
shape, substituting the generated title for `<generated-title>`:

```json
{
  "data": {
    "search": {
      "error": null,
      "page": 1,
      "per_page": 1,
      "query": "<generated-title>",
      "query_type": "Book",
      "results": [
        {"title": "<generated-title>"}
      ]
    }
  }
}
```

No unknown envelope, search, or result keys are accepted. The echoed query,
type, page, and page size must equal the fixed request. A single non-empty
bounded title is canonicalized into provider evidence. A successful response
with an empty `results` array maps to `provider_empty`; HTTP `404` maps to
`provider_no_match`. HTTP `401`, `403`, `429`, or `500`, a non-null search
error, a GraphQL `errors` envelope, timeout, TLS/DNS/peer failure, oversize,
invalid JSON, shape drift, unknown fields, multiple results, or an invalid
title maps to `provider_failed`. Provider failure never erases local F06
evidence or becomes negative bibliographic evidence.

## Secret and persistence boundary

Omitting `--secret-reference` constructs no adapter or client, resolves no
secret or DNS, performs no egress, exits `0`, and persists
`provider_disabled`. A malformed reference is syntax failure and exits `2`
before state, secret, or network access. A syntactically valid reference whose
file is missing, insecure, unreadable, or invalid performs no request, exits
`0`, and persists `provider_failed`.

The secret reference must be an absolute owner-only regular file, not a
symlink, with 1 through 2,048 bytes containing a printable non-whitespace ASCII
token. The provider credential is isolated from target and reviewer
credentials. Tokens, secret paths, raw traffic, headers, request bodies,
upstream identifiers, and provider caches are never logged, reported, or
persisted.

Canonical observations receive deterministic non-secret identities beginning
`hardcover-poc-`. Identical observations replay; different disabled, failed,
empty, no-match, disagreeing, and agreeing outcomes coexist without collision or
supersession. All state remains `synthetic_preview` and structurally derives
`production_applicable=false`.

F09b changes no `config-v1` field and introduces no environment secret,
encrypted store, provider registry, general egress broker, cache, ranking,
live-target binding, multi-provider framework, AI, decision, or write surface.
Fake-transport qualification is required. A real-endpoint smoke test is
optional and becomes required only before a live Hardcover compatibility claim.
