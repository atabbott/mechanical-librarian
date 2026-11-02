# ADR-0091: Decode every browser response and report uncertain writes truthfully

- Status: proposed
- Date: 2026-11-01
- Owners: project maintainers
- Issues: #1577 (stage 1), #1571 (client half of uncertain outcomes)
- Amends: ADR-0043 (HTTP module and drift), ADR-0044 (build identity delivery)
- Supersedes: none
- Superseded by: none

## Context

The browser's HTTP module restates every server response shape by hand. The
server's response models are strict and closed, but the browser checks them
only where a mapper happens to look. Two further gaps decide what the owner is
told:

- A browser tab running the previous build is stopped only when it writes.
  Reads were never checked against the installed build, so an old tab kept
  rendering responses it may no longer understand.
- An unsafe request whose write committed could still be reported as refused:
  a fault after the commit answered with a 4xx, or a caller showed "nothing
  changed" for every error.

## Decision

1. **Generated decoders.** An in-repository generator emits response decoders
   from the OpenAPI document, with no runtime dependency, and the HTTP module
   decodes every JSON response through them. Generated files are not
   committed. Delivered in later stages of the same project.
2. **Required properties.** Every response property is required except a
   pinned list of omissions. Delivered with the decoders.
3. **The HTTP module keeps** transport, view mapping, and the checks a schema
   cannot state, each named.
4. **Uncertain outcomes.** An unsafe request whose outcome the browser cannot
   know is reported as possibly applied, never as refused, and the caller
   reloads the authoritative state. The browser half is #1571's
   `uncertainWriteOutcome` and `PRE_CALL_SERVER_CODES`: a lost
   acknowledgement, or a 5xx not proven to be raised before the handler ran.
   The server half is that no failure after a committed write is answered
   with a 4xx. `tests/unit/test_unsafe_outcome_inventory.py` pins, for every
   unsafe `/api/v1` route, each error status and code and the latest point it
   can be raised, and fails for an unclassified route. A decision's same-key
   Retry is the one place a repeat is offered, because the decision route
   replays a recorded decision instead of recording a second one.
5. **Build identity on authenticated responses.** The server sets
   `x-ml-build-identity` on `/api/v1` responses to requests that authenticated
   an owner session, and the browser compares it on every response, read or
   write. A mismatch raises the shell's reload notice, and a route that fails
   on it offers "Reload the current application" instead of another try.
   - **Disclosure rule.** The identity is a non-secret version gate, not a
     credential. It is disclosed only to the audience that already receives
     it in the authenticated shell.
   - It is never sent on a 401, on `/api/v1/session/login`, in asset-recovery
     mode, on the trust boundary's early 400 and 403 answers, on a body-limit
     413, or to a request without a valid session. An unhandled 500 carries
     no header.
   - A route marks the request once `_require_session` authenticates it. An
     unmatched `/api/v1` path is checked with one session read and no write.

## Alternatives rejected

- A schema library at runtime, or a table interpreter: both add a dependency
  or a second schema language for what the OpenAPI document already states.
- Keeping the hand-written mappers as the response check: drift stays
  invisible until a field is read.
- The identity on every response: an unauthenticated caller could then read
  the installed version.
- Per-caller error branches without a shared predicate: they are easy to miss.

## Consequences

### Positive

- An old tab that is still signed in sees the reload notice on its next
  request; one whose session has expired goes to sign-in, which serves the new
  shell.
- A failure after a committed write reads as "could not be confirmed" with
  the current state reloaded, instead of a false refusal.

### Negative

- A provider-derived validation failure before the OpenRouter key is saved is
  now answered 500 and read as uncertain, though nothing was saved. This is
  the conservative direction.
- Every authenticated read now sends one more header.

### Neutral or deferred

- The decoders, the required-property rule and the removal of the
  hand-written checks follow in later stages. ADR-0043's HTTP-module text is
  edited when the decoders replace the mappers.
