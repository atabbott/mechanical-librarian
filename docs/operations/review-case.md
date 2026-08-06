# Review-case admission

Admit one exact current ProposalSet:

```sh
mechanical-librarian review-case-admit --config config.json --database reports.sqlite \
  --artifact-store artifacts --proposal-set-id proposal_set_<64-lowercase-hex-characters>
```

The typed canonical result is `admitted`, `replayed`, `superseded`, `no_case`,
`retired`, or `stale`. `propose_change`, `acceptable_choice`, and
`unresolved_choice` results create a case; `no_case` and `retired` have no
empty case. Untrusted failures are redacted as fixed `invalid`,
`incompatible`, `currentness`, or `persistence` codes.
`superseded` and `retired` require an active prior case. After a `no_case` or
`retired` checkpoint, the next actionable set is `admitted` and another
nonactionable set is `no_case`.
An unavailable external currentness preflight is `currentness`; repository
read or write failure remains `persistence`.

The complete `review-case-v2` case binds the exact ProposalSet, report, target
and file identity. It also projects the exact
`bibliographic-relationships-v1` document. Preserve selections are rechecked
against the bound target field, so a changed value or changed missing/null/empty
state changes case identity and cannot replay under an old combination.
Per-destination review proposal IDs bind the report, target, and exact result
set independently of the relationship projection. Adding or removing a
relationship tuple therefore changes ProposalSet, case, packet, and request
identity without renaming an otherwise unchanged field proposal. A rejected
tuple makes all three title/subtitle/series proposal projections explicitly
manual, including exact target-bound companions whose raw result has no
invented alternative.
Inside the admission transaction, the service recomputes the fixed F09o
evaluator output from the bound report; a synthetic or otherwise different
ProposalSet, including any changed or omitted compatible-combination tuple, is
`incompatible` and publishes no review state. A report or media
pointer that loses currentness returns `stale`.
Lineage checkpoints retain an attributable fixed-system audit transition. A
newer report makes an earlier case historical without rewriting it. Use a
clean rebuild and regenerate supported reports/ProposalSets for an older
database; admission itself performs no decision, apply, or write, and no
migration command exists.

```sh
mechanical-librarian review-case-get --database reports.sqlite \
  --review-case-id review_case_<64-lowercase-hex-characters>
```

Get returns the immutable case and derived currentness without writing it.
Both commands validate the complete canonical typed result before printing
success. Earlier schemas are rejected under the clean-state policy.

## Authenticated exact-current inspection

With an exact-current application database and explicit `review` configuration,
the supported serving boundary terminates TLS directly in `review-serve`:

```sh
mechanical-librarian review-serve --config config.json \
  --database reports.sqlite --host 127.0.0.1 --port 8443
```

The configured `canonical_origin` must name that exact external HTTPS origin,
including a non-default port. Configure both `tls_certificate_file` and
`tls_private_key_file` as absolute paths to one matching identity, and make
the certificate owner-controlled and the private key owner-only regular files
(`chmod 644` and `chmod 600`, respectively). Missing, linked, writable-by-other,
unreadable, malformed, or mismatched inputs fail before the database or network
listener opens. There is no supported HTTP fallback. Forwarded proxy identity
is not accepted by Uvicorn; application proxy trust remains an explicit,
bounded allow-list and should normally stay empty for direct TLS.

For an isolated development qualification only, a synthetic one-day identity
can be created and then referenced from the two configuration fields:

```sh
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout review-test.key -out review-test.crt -days 1 \
  -subj '/CN=127.0.0.1' -addext 'subjectAltName=IP:127.0.0.1'
chmod 644 review-test.crt
chmod 600 review-test.key
curl --cacert review-test.crt https://127.0.0.1:8443/health/live
```

This synthetic command is not production certificate management. Operators
remain responsible for obtaining, rotating, and securely mounting a certificate
whose names cover the canonical origin; the application neither issues nor
renews certificates. Container health probes must use the same HTTPS liveness
URL and trust the mounted certificate rather than downgrading the endpoint.

Only `/health/live` is anonymous operational data. The login surface verifies
the owner-only Argon2id hash file, applies a durable bounded throttle, and
creates an opaque server-side session whose token is stored only as a digest.
All review responses are private/no-store and enforce the canonical HTTPS Host
and Origin, restrictive CSP, secure HttpOnly SameSite cookies, CSRF on logout
and decisions, and explicit proxy trust. The `/api/v1` surface exposes
exact-current case detail, bounded evidence, current-case admission and decision
history, one individual field-decision operation, and authenticated OpenAPI.
The mutation requires the displayed proposal ID and target fingerprint, an
explicit `accept`, `keep_current`, or `reject` outcome, a 16–128 character
`Idempotency-Key`, the session CSRF token, and an exact proposed value for
`accept`. Same-key/same-payload retries return the original acknowledgement;
conflicts and stale state fail without a partial decision or event.
The generated OpenAPI marks the owner session cookie and required mutation
headers explicitly. Decision attempts for absent case IDs return `404`, while
invalid sessions return `401`, CSRF mismatches return `403`, and persistence
outages return `503`. Logout clears the cookie only after revocation succeeds.
Authenticated `/library` provides bounded attention-first, all, and
no-attention-needed lenses; `/attention` contains only exact unresolved/manual
fields and links to the exact field on detail. Both support bounded search,
maximum 50-item pages, and query-bound cursors. Automatic-only cases remain
inspectable in the library but never gain a human control. Decision persistence
cannot create automatic authority, construct an apply plan, construct a target
client, perform a write, reverse a decision, or widen one exact ordinary field
into a bulk operation.

The login request body is capped at 2 KiB at the ASGI boundary before JSON
parsing. Five reserved attempts within five minutes close the login gate; the
reservation is recorded before Argon2 work so concurrent requests cannot
outpace the bound. A successful owner login clears the failure window.
