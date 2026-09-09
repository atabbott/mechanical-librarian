# ADR-0071: Explicit local transport

- Status: accepted
- Date: 2026-09-09
- Issue: #844

## Context

A container beside a self-hosted Audiobookshelf service commonly reaches an
explicit HTTP service origin. Requiring certificate infrastructure for both
that connection and the owner workspace prevents a simple local deployment.
Browser trust, listener TLS termination, and target transport are separate
boundaries.

## Decision

The configured Audiobookshelf endpoint scheme alone selects HTTP or HTTPS.
HTTPS verifies its certificate and hostname. Credentials remain confined to
that exact origin's supported routes and methods; redirects and ambient
proxies remain disabled. Provider, cover and reviewer transport is unchanged.

The canonical browser origin selects one of three closed modes: HTTP without
TLS paths; direct HTTPS with a paired identity; or HTTPS without a local
identity and with at least one exact trusted proxy peer. The latter listener is
HTTP. The trusted edge must send exactly one complete canonical
X-Forwarded-Host/Proto pair. Duplicate Host or forwarding fields, partial pairs,
chains, Forwarded, and untrusted forwarding fail closed. Setup shares the same
boundary. Uvicorn proxy interpretation remains disabled.

Secure cookie creation/deletion and HSTS follow the canonical browser scheme,
including setup and early/error responses. Session HttpOnly, SameSite Strict,
CSRF, exact-origin checks, throttling, body and connection limits, build identity,
and shutdown remain. Direct TLS identity validation precedes database and
listener creation and retains its descriptor-bound lifetime. Readiness selects
HTTP or pinned TLS from the actual listener identity.

Fresh Compose publishes `127.0.0.1:8080:8443` and explicitly mounts a prepared
owner-only bootstrap. HTTP is for an owner-controlled local network or VPN;
passwords, cookies, metadata and guarded-effect traffic are plaintext. Direct
or proxied HTTPS needs a certificate the browser already trusts. No trust
installation, interstitial bypass, automatic TLS fallback, verification
exception, certificate service or new write authority is introduced.

Configuration is exact-current `config-v9` and `review-bootstrap-v2`. Clean
rebuild required for these configuration documents means explicit replacement,
not conversion or adoption. Database, owner-settings, setup, session, report
and API schemas are unchanged by this decision.

## Consequences

A local owner can use the complete workspace without certificate infrastructure.
The browser's passive HTTP indicator remains truthful. Operators own network
exposure, proxy topology, certificate trust and renewal. HTTP does not claim
internet-safe transport, and proxied HTTPS protects only the browser-to-edge hop.

This decision supersedes the HTTPS-only browser/cookie/HSTS provisions of
[ADR-0030](0030-authenticated-review-web-boundary.md), the TLS-only listener and
readiness provisions of [ADR-0038](0038-bounded-single-container-operations.md),
and the required bootstrap TLS fields of
[ADR-0058](0058-settings-authority-and-first-boot.md). Their remaining authority,
security and first-boot provisions continue to apply.
