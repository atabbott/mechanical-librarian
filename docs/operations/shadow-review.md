# Qualification-only shadow review

This development surface produces untrusted advice for one exact current
ReviewCase. It cannot decide, approve, apply, write to Audiobookshelf or media,
run in the background, retry automatically, or activate normal review.

## Qualification profile

Create an owner-only (`0600`) canonical JSON file matching
[`reviewer-qualification-profile-v1`](../reference/reviewer-qualification-profile-v1.schema.json).
It declares absolute local paths and exact SHA-256/version identities for a
maintainer-provisioned Codex CLI and Bubblewrap, a private `0700` auth-store
directory, an empty-job parent, and explicit resource limits. Paths and auth
material are never stored in the application database. The tuple is fixed to:

- model `gpt-5.6-sol`, reasoning `medium`;
- prompt `shadow-review-prompt-v1`;
- profile `codex-shadow-profile-v1`;
- packet `shadow-review-packet-v1`;
- output `shadow-review-result-v1`;
- policy `qualification-only-policy-v1`; and
- sandbox `bubblewrap-v1`.

The runtime never installs or upgrades either artifact. A missing, changed, or
wrong executable is `setup`; an unknown tuple or malformed profile is
`profile`. Codex CLI and Bubblewrap are external qualification prerequisites,
not Python dependencies. Record their licence/provenance with the private
qualification evidence; F15a owns supported bundling, licence inventory, and
SBOM inclusion.

## Authentication

All auth and review processes serialize on one owner-only auth-store lock:

```sh
mechanical-librarian reviewer-auth-start --qualification-profile profile.json
mechanical-librarian reviewer-auth-status --qualification-profile profile.json
mechanical-librarian reviewer-auth-logout --qualification-profile profile.json
```

Start uses device authentication. Status accepts only the exact ChatGPT
subscription state; API-key authentication is rejected. Logout is explicit and
never performed by preview, request, inspect, or reevaluate.

## Preview and request

Preview validates only profile bytes and the exact persisted case lineage. It
does not inspect declared executables or auth state, construct a target,
provider, reviewer, or other network client, or invoke Codex:

```sh
mechanical-librarian shadow-review-preview --database reports.sqlite \
  --review-case-id review_case_<64-lowercase-hex> \
  --qualification-profile profile.json
```

Preview is not fresh eligibility proof. Request atomically revalidates the
current persisted case/report/proposal/profile/packet, persists the request and
attempt, then performs at most one foreground invocation:

```sh
mechanical-librarian shadow-review-request --database reports.sqlite \
  --review-case-id review_case_<64-lowercase-hex> \
  --qualification-profile profile.json
```

An identical request command reopens state and never egresses again. Inspect is
offline:

```sh
mechanical-librarian shadow-review-inspect --database reports.sqlite \
  --review-request-id review_request_<64-lowercase-hex> \
  --qualification-profile profile.json
```

A successful status is permanently `qualification_only`. A dispatched
timeout, cancellation, runtime, or output failure remains `unknown`, because
the external service may have received the packet. There is no silent retry.
After the supervised Linux process tree is proved absent, an operator may make
one explicit new attempt:

```sh
mechanical-librarian shadow-review-reevaluate --database reports.sqlite \
  --review-request-id review_request_<64-lowercase-hex> \
  --qualification-profile profile.json
```

The reevaluation claim compares the exact latest attempt inside the write
transaction, so concurrent operators cannot both append or overlap. If a
process ended after durable dispatch but before a terminal outcome was stored,
inspection reports `unknown`. If the application stopped before dispatch, the
attempt remains `pending`; explicit reevaluation first proves the exact
persisted claimant PID and Linux start token absent, then may safely claim its
successor. Any unreleased controller could not execute or egress.

Executable paths are opened, verified, and held by descriptor through version,
auth, and sandbox invocation. Authentication also ignores auth-store
configuration and supplies exact ChatGPT/file-store/update/telemetry
constraints, so a local `config.toml` cannot widen the auth invocation.

Failures are the value-free codes `runtime`, `setup`, `auth`, `profile`,
`timeout`, `cancel`, `output`, or `currentness`. Raw stdout, stderr, reasoning,
malformed content, profile paths, and auth data are discarded.
