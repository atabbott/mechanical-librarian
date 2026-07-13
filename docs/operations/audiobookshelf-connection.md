# Audiobookshelf connection check

`connection-check` is a report-only F04 diagnostic for one isolated configured
Audiobookshelf target. It resolves an API key from a private regular file,
performs the pinned status and authorization probes, and reports generic
compatibility and capability states. It does not open application state, list a
library, read an item or content, construct provider/reviewer clients, or obtain
mutation authority.

The command validates the complete `config-v1` document but consumes only the
single configured Audiobookshelf connection. Validated provider, reviewer, and
notification settings may coexist without constructing those components, and
configured read-only capability overrides do not replace the capability
evidence observed by this report.

Create the credential file with owner-only permissions and place its absolute
path in the existing `connections.secret_reference` field:

```sh
install -m 600 /dev/null /run/secrets/audiobookshelf-api-key
mechanical-librarian connection-check --config /config/config.json
```

The file contains only the API key. Audiobookshelf API keys use the Bearer
scheme for automation from server 2.26.0 onward. Environment variables,
ambient proxy settings, cookies, redirects, and credential-bearing URLs are not
used. Replacing the file takes effect on the next invocation; material is not
cached.

Normal configured targets must use HTTPS. Plain HTTP is rejected before secret
resolution or client construction except for literal `localhost`, `127.0.0.0/8`,
or `::1` loopback targets used by disposable local qualification. The Bearer
header is attached only to the exact authorization POST and is never sent to
status or any other route.

Success output contains only the configured adapter/connection identifiers,
version/compatibility, adapter-contract and fixture revisions, closed
capability states, and `authority=target-connection-read-only`. Failure output
is one fixed value-free line.

The 2.35.1 fixture contract permits exactly `GET /status` and observational
`POST /api/authorize` with Bearer authentication. The authorization POST has no
body or mutation semantics. Every redirect and every other route or method is
rejected. The stale upstream API reference describing `GET /api/authorize` is
not treated as current evidence.

Authorization scope is compared only with the configured `enabled_libraries`
identifiers. A profile is complete when it grants all-library access or its
fully validated accessible-library list covers every configured library.
Audiobookshelf library identifiers are canonical lowercase UUIDv4 values and
are preserved exactly as opaque, adapter-scoped identities.
Missing, partial, malformed, inactive, or mismatched profiles are degraded and
cannot declare supported read capabilities; the check still performs no
library or item request.

## Disposable qualification

Qualification uses only synthetic temporary storage and the digest-pinned
image `ghcr.io/advplyr/audiobookshelf@sha256:1eef6716183c52abafe5405e7d6be8390248ecd59c7488c44af871757ac8fc4d`.
Initialization and API-key creation are fixture setup, not part of the F04
probe. A local trace must show the exact two-request sequence above and no
library, item, cover, media, upload, update, or delete route.

Run `PYTHONPATH=src uv run --frozen python
scripts/qualify_audiobookshelf_connection.py`. The harness performs exact
setup/probe/trace/teardown, binds only a random loopback port, creates a
synthetic root and disposable API key in temporary volumes, routes the F04
probe through a local trace proxy, asserts exactly `GET:/status` and
`POST:/api/authorize`, removes the container, and deletes the temporary tree.

Never use this qualification path with production data, credentials, or
volumes.
