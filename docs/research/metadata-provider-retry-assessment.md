# Metadata-provider retry policy

Google Books, Hardcover and Open Library each retry a narrow set of transient
failures, with different counts. No provider publishes a retry count, so each
count is Mechanical Librarian's own bounded choice, justified below from the
provider's published guidance.

| Provider | Attempts per request | Attempts per observation | Retried | 429 |
| --- | --- | --- | --- | --- |
| Google Books | 6 (five retries) | 54 | 503 and transient transport failures | Never retried |
| Hardcover | 3 (two retries) | 27 | 500, 502, 503, 504 and transient transport failures | Retried only when a valid wait fits the deadline |
| Open Library | 2 (one retry) | 49 (at most 4 retries) | 500, 502, 503, 504 and transient transport failures | Never retried |

The full provider egress and retry contract, including the other providers, is
in [security: metadata providers](../architecture/security.md#metadata-providers).
Per-provider request ceilings are in
[metadata report: provider limits](../operations/metadata-report.md#provider-limits).

## Shared rules

All three adapters retry through one bounded session
(`adapters/providers/sdk.py`):

- **Backoff.** Truncated exponential backoff with fresh jitter: about 1, 2, 4,
  8 and 16 seconds, each plus up to one second of jitter, capped at 16 seconds.
- **`Retry-After`.** A valid value of at most 60 seconds, as a delay or an HTTP
  date, replaces the backoff. Anything else is ignored.
- **Deadline.** A retry happens only if its delay ends before the provider's
  deadline. Every attempt also waits for the provider's one-second
  request-start pacer and counts against the request and byte ceilings.
- **Transient transport failures.** Connect and read timeouts; remote-protocol
  errors raised by the HTTP stack; and connect, read and write errors whose
  cause is a reset, abort, broken pipe, refused or unreachable connection,
  timeout, or temporary DNS failure (`EAI_AGAIN`). Exception text is never
  inspected.
- **Never retried.** Any cause chain containing a TLS error; proxy,
  configuration, invalid-URL and local protocol errors; decoding failures;
  malformed or oversized responses; GraphQL errors in a 200 response;
  authentication, authorisation and other 4xx responses except the bounded
  Hardcover 429; and application validation failures.
- **Exhaustion.** The last failure keeps its classification (`unavailable` or
  `rate_limited`). It is never turned into a no-match.
- **Records.** Each attempt records only its category, delay, elapsed time and
  outcome, never credentials, queries or response content.

## Google Books

Google Books volume search is a read-only, idempotent HTTP `GET`, so repeating
it is safe
([Books usage guide](https://developers.google.com/books/docs/v1/using#PerformingSearch),
[RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#name-common-method-properties)).
The Books documentation names no retry count and has no 503 entry
([Books API reference](https://developers.google.com/books/docs/v1/reference/)).

Five retries follows general Google API guidance. The Enterprise License
Manager guide recommends 5 to 7 retries with exponential backoff on 503. The
Directory API guide gives waits of 1, 2, 4, 8 and 16 seconds plus jitter, then
stops: five retries, six attempts
([Enterprise License Manager limits](https://developers.google.com/workspace/admin/licensing/v1/limits),
[Directory API backoff](https://developers.google.com/workspace/admin/directory/v1/limits#implementing_exponential_backoff)).
Google's Python API client retries 5xx and a bounded set of transport errors
with randomised backoff
([googleapiclient retry source](https://googleapis.github.io/google-api-python-client/docs/epy/googleapiclient.http-pysrc.html#_retry_request)).
The Cloud Storage Java client also defaults to six attempts for idempotent
operations, 503 included
([Cloud Storage retry strategy](https://cloud.google.com/storage/docs/retry-strategy)).
A short keyed probe of the Books endpoint saw runs of consecutive 503
`backendFailed` responses with no `Retry-After`, so a single retry would often
be wasted.

Only 503 is retried. A 429 or quota response is `rate_limited` and terminal; a
burst-throttle response also holds later Google Books requests for its window.

## Hardcover

Hardcover documents a limit of 60 requests a minute, a 30-second query timeout
and a beta API. It lists 429 as "try again later" and 500 as a server error, but
documents no `Retry-After`, backoff or retry count
([Hardcover API documentation](https://github.com/hardcoverapp/hardcover-docs/blob/1c5bdbd3244e02b03ccc658a792793e39cba17a8/src/content/docs/api/Getting-Started.mdx#L65-L95)).

The request is an HTTP `POST`, but it carries a fixed GraphQL query, never a
mutation. GraphQL requires query fields to be free of side effects, so a retry
is safe
([GraphQL specification](https://spec.graphql.org/September2025/#sec-Normal-and-Serial-Execution)).

Two retries is deliberately fewer than Google Books: Hardcover gives no failure
data or retry guidance, is in beta and enforces a per-minute limit. The shared
one-second pacer keeps retries within that limit.

A 429 is retried only when the response states a wait that ends before the
deadline: a valid `Retry-After`, or a reset in Hardcover's own rate-limit
headers. A daily-quota reset almost never fits, so it normally stays
`rate_limited`. HTTP allows `Retry-After` on 429 and 503
([RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3),
[RFC 6585](https://www.rfc-editor.org/rfc/rfc6585.html#section-4)), but that is
no promise Hardcover sends it.

## Open Library

Open Library describes its API as low-volume and for real-time discovery, not
bulk access. It asks for one request a second by default (three for
identified clients), and warns that heavier use may be blocked
([Open Library API usage](https://openlibrary.org/developers/api)). Its
production 429 response points clients to data dumps and sets no `Retry-After`
([429 handler](https://github.com/internetarchive/openlibrary/blob/5ee50f79d4fb355f77d375d7cb56c8cd123838c8/docker/web_nginx.conf#L115-L121)).
Its official client retries transport and 5xx failures up to five attempts and
never retries a 4xx
([Open Library client](https://github.com/internetarchive/openlibrary-client/blob/3f131881358d2c0dba5de79d894b80fae24742c6/olclient/openlibrary.py#L75-L82)).

All Open Library requests are idempotent `GET`s. One observation can make up to
45 base requests (search, edition and work details), so copying the reference
client's five attempts per request would allow 225. One retry per request and
four per observation caps the observation at 49 attempts, which respects the
low-volume guidance.

A 429 is never retried. The provider's guidance, its 429 response and its own
client all treat rate limiting as a signal to back off. The result stays
`rate_limited`, and [provider recovery](../operations/provider-recovery.md)
tries the lookup again later on its own schedule. The pacer stays at one request
a second even though identified clients may use three, which leaves headroom
for detail chains and other users of the same address.
