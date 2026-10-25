# ADR-0086: Require a deployment-held claim token for the first owner

- Status: accepted
- Date: 2026-10-25
- Owners: project maintainers
- Issue: #1383
- Supersedes: the "first visitor receives a nonce" provision of
  [ADR-0058](0058-settings-authority-and-first-boot.md)
- Superseded by: none

## Context

ADR-0058 let the first visitor of an empty `/data` volume create the owner
password. The synchronizer nonce, Origin and Host checks prove only that the
request came from the setup page on the canonical address; they say nothing
about who loaded that page. A network visitor who reaches the published port
before the owner could therefore claim the instance, stage its connection and
start preparation. The deployment already has two channels that only its
operator can read: the container's own output and the `/data` volume.

## Decision

- **Mint.** Every startup that finds setup still unclaimed mints one token with
  `secrets.token_urlsafe(32)`. Setup state stores only its domain-separated
  SHA-256 digest, as `SetupStateDocument.claim_token_digest`: 64 lowercase hex
  characters while unclaimed and `None` once claimed. The ledger validator
  refuses any other combination, and the document moves to `setup-state-v4`.
- **Order.** Startup commits the new digest first, which invalidates any
  earlier token. It then writes the token to `/data/private/setup-claim-token`
  (mode `0600`, opened without following a link and truncated), and finally
  announces it once on the container's standard output with a raw write that
  bypasses logging. A failure after the commit leaves no usable older token; a
  restart mints a fresh one.
- **Claim.** `POST /api/setup/session` on an unclaimed instance requires
  `claim_token` in addition to the existing nonce, Origin and Host checks. A
  bounded pre-check refuses a missing or wrong token before any Argon2 work,
  with the fixed `403 {"error":"claim_token"}`. The refusal changes no state,
  keeps the token valid, and does not count toward the login throttle. The
  authoritative constant-time comparison runs inside the `claim_owner`
  transaction, which also sets the digest to `None`; the token file is then
  removed. The revision compare-and-set still settles concurrent correct claims
  with one winner, and the loser still receives `409`.
- **After the claim.** A token presented to a claimed store is refused as a
  claim token, never treated as stale. A startup that finds setup claimed
  removes any remaining token file. Signing in to resume setup needs only the
  owner password.
- **Confidentiality.** The token never appears in an API response, log,
  exception text, `repr` or evidence. The setup application answers request
  validation failures with a fixed value-free `422` instead of echoing input.
- **Scope.** File-managed mode has no setup and is unchanged. Bootstrap,
  settings authority, CSRF and Origin rules are unchanged.

## Consequences

### Positive

- Claiming a new instance requires reading the container's output or its
  `/data` volume, which a network visitor cannot do.
- A restart is the recovery for a lost token, and it revokes the earlier one.

### Negative

- First run gains one copied value. The setup page names where to find it.
- Incomplete `setup-state-v3` stores are refused at open: clean rebuild
  required under ADR-0013, for setup state only. Completed owner settings are
  unaffected.

### Neutral or deferred

- Login throttling, password length, password change and session revocation are
  owned by #1384 and #1385.

## Alternatives considered

### Keep the first-visitor claim and document a private network

Rejected: the default deployment publishes the setup port before the owner
visits it, and copy cannot close that race.

### Put a claim secret in the bootstrap document

Rejected: it would make the owner author a secret by hand and widen the
deploy-side document that ADR-0058 keeps free of owner credentials.

## Validation

- Store tests prove the raw row holds only the digest, a restart rotates it and
  refuses the earlier token, and a replay after the claim is refused as a claim
  token rather than as stale.
- Web tests prove a missing or wrong token is refused before Argon2 without
  state change or throttle debt, a later correct claim succeeds, and no
  response or log carries the token.
- Composition tests prove one standard-output line and an owner-only file.
- A real-server test claims through the minted file over TLS.
