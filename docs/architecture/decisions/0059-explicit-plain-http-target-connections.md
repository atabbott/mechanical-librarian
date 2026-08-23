# ADR-0059: Admit local plain-HTTP target connections only by explicit opt-in

- Status: accepted
- Date: 2026-08-23
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The target credential boundary admits a plain `http://` Audiobookshelf
endpoint only for `localhost`/loopback; every other endpoint must be HTTPS
with full certificate verification. The product's normal deployment is one
container beside an existing Audiobookshelf container on the same private
Docker or LAN segment, reached by container name or private address.
Audiobookshelf has no native TLS and its documentation delegates TLS to a
reverse proxy at the external edge, so on the internal segment there is
usually no certificate to verify at all. The loopback-only allowance
therefore excluded the deployment shape PRODUCT.md names as normal.

An audit of comparable self-hosted projects (the Servarr family, Overseerr,
Jellyseerr, Bazarr, Tautulli, Homepage, Homarr, Komga, Kavita, Calibre-Web,
Home Assistant integrations) found that every one accepts plain HTTP for a
local companion service and none requires HTTPS locally. Where those projects
differ is how they degrade HTTPS: some ship certificate-verification disable
toggles, which this project's security posture rejects — the review surface
itself has no plaintext or TLS-disable mode, and
`docs/product/future-reviewer-backends.md` already requires an "explicit
high-visibility local-endpoint mode" for any plain-HTTP reviewer egress
rather than a verification kill-switch.

## Decision

A target connection may declare `"plain_http": true` in `config-v6`. Only
that validated, connection-scoped opt-in admits a cleartext `http://`
endpoint beyond loopback; it is threaded from the configuration snapshot into
the one client constructor in `infrastructure/security/credentials.py`, which
remains the sole enforcement point.

The opt-in widens nothing else:

- HTTPS endpoints keep full hostname and certificate verification, and no
  configuration can weaken or disable it.
- Without the opt-in, plain HTTP stays confined to `localhost`/loopback,
  exactly as before.
- Every other origin bound is unchanged under the opt-in: no path, query,
  fragment, or userinfo; credential attachment stays same-origin on the
  closed route shapes; redirects and ambient proxy trust stay off.
- `plain_http: true` on a non-`http://` endpoint fails configuration
  validation, so an opt-in cannot outlive an endpoint's upgrade to HTTPS and
  silently hold the weaker posture open.

The operator's explicit declaration is the boundary. The runtime does not
guess locality by resolving hostnames or classifying private address ranges:
resolution-based admission is rebinding-sensitive and a hostname's locality
is not observable from configuration, while the explicit flag is exact,
auditable, and fails closed.

## Consequences

### Positive

- The normal Compose deployment (`http://audiobookshelf:13378` by container
  name) is supported without a reverse proxy or synthetic certificate.
- The default posture is unchanged; existing configurations keep their exact
  meaning, and the field is additive so `config-v6` documents remain valid.
- The declaration is visible in the configuration document an operator
  reviews, not implicit in address classification.

### Negative

- Under the opt-in, the connection's bearer token and library metadata cross
  the declared segment in cleartext; an attacker on that segment can read and
  replay them. The reference documentation states this cost and confines the
  opt-in to segments the operator controls.

### Neutral or deferred

- Trusting a private certificate authority per connection (the
  Homarr/`NODE_EXTRA_CA_CERTS` pattern) would serve operators who do run
  internal TLS; it remains future work and is not a substitute this decision
  forecloses.
- The reviewer-backend local-endpoint mode described in
  `docs/product/future-reviewer-backends.md` remains its own future decision;
  this ADR covers only the target connection.

## Alternatives considered

### Automatic allowance for private address ranges

Admitting plain HTTP whenever the host is loopback, RFC 1918, or a
single-label name needs no configuration, but locality is not observable
from a hostname without resolving it, resolution-time admission is
DNS-rebinding-sensitive, and the allowance would be invisible in the
configuration document. Rejected in favour of the explicit declaration.

### Certificate-verification toggle

A per-connection "trust self-signed certificates" switch is the other common
ecosystem pattern. It normalizes broken TLS, contradicts the existing
no-TLS-disable posture, and secures nothing an explicit plain-HTTP
declaration does not state more honestly. Rejected.

### Keep the loopback-only rule

Preserves the strictest posture but leaves the product's normal deployment
shape unable to connect, pushing operators toward TLS-terminating sidecar
proxies that add attack surface without adding assurance on a single-host
segment. Rejected.

## Validation

- Unit tests prove the default rejects non-loopback plain HTTP, the opt-in
  admits it, the opt-in widens no other origin bound, credential attachment
  stays same-origin under the opt-in, and a `plain_http` flag on a
  non-`http://` endpoint fails validation.
- The generated `config-v6` schema declares the field with default `false`,
  and the schema-currency test pins it.
