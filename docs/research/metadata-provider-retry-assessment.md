# Metadata-provider retry assessment

Date: 2026-07-28

## Question

After adopting five retries for Google Books, should Mechanical Librarian also
retry transient failures from Hardcover and Open Library?

## Summary recommendation

Do not apply the Google Books count uniformly:

| Provider | Recommended policy |
| --- | --- |
| Google Books | Five retries, six total attempts, as assessed in [Google Books 503 retry policy](google-books-retry-policy.md). |
| Hardcover | Two retries, three total attempts, for a narrow transient class. Add a shared one-request-per-second pacer. Keep 429 terminal unless a valid `Retry-After` fits the remaining deadline. |
| Open Library | One retry per request, with no more than four retry attempts shared across one provider observation. Preserve the existing one-second pacer. Never retry 429 automatically. |

The Hardcover and Open Library counts above are Mechanical Librarian design
recommendations. Neither provider publishes those exact policies. Open
Library's official reference client provides a more permissive precedent of
five total attempts for non-4xx request failures, but copying that behavior per
request would increase this adapter's 45-request envelope to 225 theoretical
attempts and conflict with Open Library's low-volume guidance.

## Current Mechanical Librarian contract

Hardcover makes one credential-bearing GraphQL request per admitted lane, up
to nine requests per provider observation. Open Library can make one Search
request plus two edition and two distinct work-detail requests per lane, up to
45 metadata requests. Both share a 2 MiB response-byte aggregate and a
60-second aggregate deadline. Open Library alone has a shared request-start
pacer, currently fixed at one second; all requests are single-attempt
([security contract](../architecture/security.md#credentials-and-egress),
[metadata report contract](../operations/metadata-report.md)).

The Hardcover adapter maps 401, 403, and 429 separately and maps every other
non-200 status and every caught HTTP/OS error to `unavailable`; it does not
inspect `Retry-After`
([Hardcover adapter](../../src/mechanical_librarian/adapters/providers/hardcover_metadata.py#L228)).
The Open Library adapter similarly maps 429 to `rate_limited`, every other
non-200 response and caught transport failure to `unavailable`, and does not
inspect `Retry-After`
([Open Library adapter](../../src/mechanical_librarian/adapters/providers/open_library_metadata.py#L393)).

## Hardcover

### Authoritative provider guidance

Hardcover documents a 60-request-per-minute limit, a maximum query timeout of
30 seconds, and an API that is still in beta. Its response table identifies
429 as “Too Many Requests, try again later” and 500 as “Internal Server
Error.” It does not document 502, 503, 504, `Retry-After`, an exponential
backoff algorithm, or a retry count
([Hardcover API documentation at commit `1c5bdbd`](https://github.com/hardcoverapp/hardcover-docs/blob/1c5bdbd3244e02b03ccc658a792793e39cba17a8/src/content/docs/api/Getting-Started.mdx#L65-L95)).

Mechanical Librarian sends a GraphQL `query`, not a mutation, even though the
transport method is HTTP `POST`
([Hardcover query source](../../src/mechanical_librarian/adapters/providers/hardcover_metadata.py#L45)).
The GraphQL specification requires fields other than top-level mutation fields
to be side-effect-free and idempotent. Retrying this fixed query is therefore
semantically safe even though HTTP does not classify `POST` itself as
idempotent
([GraphQL specification, normal and serial execution](https://spec.graphql.org/September2025/#sec-Normal-and-Serial-Execution),
[HTTP method registry](https://www.iana.org/assignments/http-methods/http-methods.xhtml)).

### Recommendation

Add at most two retries per fixed query, for three total attempts, for:

- HTTP 500, 502, 503, and 504;
- `httpx.ConnectTimeout`, `ReadTimeout`, `ConnectError`, `ReadError`,
  `WriteError`, and `RemoteProtocolError`;
- a distinctly classified connection reset, premature EOF, or transient DNS
  failure represented by those HTTPX transport exceptions.

HTTPX itself exposes connection retries only for `ConnectError` and
`ConnectTimeout`; its documentation says broader cases such as read/write
errors and 503 require application-level retry handling
([HTTPX transports](https://www.python-httpx.org/advanced/transports/#http-transport),
[HTTPX exception hierarchy](https://www.python-httpx.org/exceptions/)).
Mechanical Librarian can safely include the additional failures only because
the operation is a fixed idempotent GraphQL query.

Do not retry 401, 403, 404, malformed GraphQL responses, GraphQL validation or
execution errors returned in a valid 200 response, invalid URLs, local
protocol errors, TLS certificate/hostname verification failures, decoding
failures, oversized responses, or application validation failures.

Treat 429 separately from transient-service retry. Hardcover says to try again
later but publishes neither a reset header contract nor a wait duration. Honor
a syntactically valid `Retry-After` only if it fits the aggregate deadline;
otherwise retain the terminal `rate_limited` result. HTTP permits
`Retry-After` on 503 and 429 responses, but that generic permission is not
evidence that Hardcover sends it
([RFC 9110, `Retry-After`](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3),
[RFC 6585, 429](https://www.rfc-editor.org/rfc/rfc6585.html#section-4)).

Add one Hardcover request-start pacer shared across the entire foreground
batch, with starts at least one second apart. That converts the published
60-per-minute ceiling into a proactive client constraint and prevents retries
from turning a current five-item maximum of 45 base requests into a burst.
With two retries per request, the provider-observation request authority would
rise from nine to 27; the aggregate deadline must still win before any sleep or
attempt.

The choice of two retries is deliberately smaller than Google Books' five:
Hardcover supplies no failure-streak evidence or retry-count recommendation,
is beta, and imposes a documented per-minute limit. This count should be
revisited only with redacted attempt telemetry.

## Open Library

### Authoritative provider guidance

Open Library describes its APIs as low-volume and intended for infrequent,
real-time discovery rather than as a third-party data backend. It asks clients
to cache responses, identify themselves, avoid hundreds of single-book
requests, and use data dumps for bulk access. It publishes a default limit of
one request per second and an identified-client limit of three requests per
second; violations may trigger aggressive rate limiting or blocking
([Open Library API usage and rate limits](https://openlibrary.org/developers/api)).

Open Library's checked-in production Nginx configuration defines API pacing
and returns a JSON 429 response that directs clients toward data dumps. The
429 handler does not explicitly add `Retry-After`
([rate-limit zones at commit `5ee50f7`](https://github.com/internetarchive/openlibrary/blob/5ee50f79d4fb355f77d375d7cb56c8cd123838c8/docker/nginx.conf#L95-L104),
[API limiter and 429 handler](https://github.com/internetarchive/openlibrary/blob/5ee50f79d4fb355f77d375d7cb56c8cd123838c8/docker/web_nginx.conf#L115-L121),
[API location](https://github.com/internetarchive/openlibrary/blob/5ee50f79d4fb355f77d375d7cb56c8cd123838c8/docker/web_nginx.conf#L191-L220)).

Its official Python reference client uses exponential backoff with
`max_tries=5` for `requests.exceptions.RequestException`, gives up on every
HTTP 4xx response, and calls `raise_for_status()` for its GET helper. This
amounts to five total attempts for transport and 5xx failures but no retry for
429
([Open Library client policy at commit `3f13188`](https://github.com/internetarchive/openlibrary-client/blob/3f131881358d2c0dba5de79d894b80fae24742c6/olclient/openlibrary.py#L75-L82),
[GET helper](https://github.com/internetarchive/openlibrary-client/blob/3f131881358d2c0dba5de79d894b80fae24742c6/olclient/openlibrary.py#L171-L178)).

Mechanical Librarian's Open Library metadata requests are all HTTP `GET`;
HTTP defines GET as safe and idempotent
([HTTP method registry](https://www.iana.org/assignments/http-methods/http-methods.xhtml)).

### Recommendation

Add retries, but use a narrower application envelope than the reference
client:

- retry HTTP 500, 502, 503, and 504 and the same selected transient HTTPX
  transport failures listed for Hardcover;
- permit at most one retry for any individual Search, edition, or work-detail
  request;
- permit at most four retry attempts in total across one Open Library provider
  observation;
- make every retry pass through the existing shared one-second pacer and the
  aggregate deadline.

This raises the declared worst-case observation authority from 45 to 49
requests, rather than to 225 if the official client's five-attempt policy were
copied independently onto every request. It preserves useful recovery for a
small number of transient failures without contradicting the provider's
low-volume, no-hundreds-of-single-book-requests guidance.

Never automatically retry Open Library 429. The provider's usage guidance,
checked-in 429 response, and official client all treat rate limiting as a
signal to reduce or change access rather than immediately repeat the request.
Retain the terminal `rate_limited` result and require a later operator-created
run. Also do not retry 404, other 4xx, malformed or oversized responses,
invalid canonical keys, detail-join failures, or application validation
failures.

Keep the current one-second pacer even though the identified-client allowance
is three requests per second. The stricter pace is appropriate for a
multi-request detail chain and provides headroom for other users of the same
source address. Backoff and pacing are cumulative: an attempt may start only
after both its retry delay and the pacer's next-start time.

## Cross-provider implementation constraints

The following are recommendations, not provider-published requirements:

- use truncated exponential backoff with fresh jitter;
- validate `Retry-After` as either a bounded non-negative delay or a valid HTTP
  date, reject ambiguous or excessive values, and never sleep beyond the
  aggregate deadline;
- check the deadline before sleeping, after sleeping, and immediately before
  network dispatch;
- count every attempted response against the request authority and every
  received byte against the existing aggregate byte ceiling;
- preserve the terminal provider classification after exhaustion rather than
  manufacturing a negative result;
- record only provider name, attempt number, failure class, bounded delay,
  elapsed time, and outcome, never credentials or private query content.

Retry classification must inspect concrete HTTPX exception types rather than
the adapters' current broad `(httpx.HTTPError, OSError, ValueError)` catches.
In particular, `LocalProtocolError`, `UnsupportedProtocol`, `InvalidURL`,
proxy/configuration errors, TLS verification errors, content decoding errors,
and local stream-state errors are not transient network failures
([HTTPX exception hierarchy](https://www.python-httpx.org/exceptions/),
[HTTPX TLS verification](https://www.python-httpx.org/advanced/ssl/)).

Any implementation changes the documented security request maxima and the
single-attempt product contract. It therefore requires coordinated security,
operations, tests, and request-budget updates; this note does not itself grant
that runtime authority.
