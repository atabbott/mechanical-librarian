# Security and threat model

## Authority

`metadata-report` may read one configured target item, one selected read-only
EPUB, owner-only credential files, and one fixed request per applicable
provider. It may append a neutral report and update its current pointer. It has
no target-write, media-write, review, decision, apply, background, retry,
deletion, migration, or repair authority.

## External actors

- A configured target or provider may return hostile bytes. Responses pass
  through size bounds, strict decoding, and closed semantic projections before
  reduction to typed facts. One provider cannot erase local or sibling-provider
  evidence.
- A mounted-media owner may provide a hostile EPUB or replace it during an
  invocation. The descriptor-bound isolated child has no credentials, network,
  persistence, matching, or decision authority. The parent validates file
  content and target identity again immediately before publication.
- A local actor may replace or corrupt configuration, secret, media, or SQLite
  files. No-follow regular-file checks, owner-only secret permissions, exact
  schema validation, canonical row decoding, envelope/document agreement, and
  fail-closed diagnostics bound this risk. 0.4 does not repair tampered state.

Ordinary typed in-process collaborators are trusted to honor their interfaces;
arbitrary descriptor, subclass, traceback, or interpreter-control-flow
sabotage is outside the supported threat model.

## Credentials and egress

Secret material is read only in infrastructure and represented nowhere in the
report. Target authorization is attached only to GET current-item requests at
the configured origin. Provider authorization is attached only to the fixed
HTTPS Hardcover GraphQL URL. HTTP clients ignore ambient proxy/environment
configuration and redirects.

Disabled providers cause no secret access, client construction, or egress.
Unreferenced providers are never bound. A provider with no usable ISBN input
returns `insufficient_input` without egress. There is no automatic retry.

## Output and persistence

Successful output and persisted documents contain only bounded target names,
item identity, opaque file identity/digest, current title, fact-local opening
excerpts, typed claims, provider candidate fields, and comparison facts. They
contain no credentials, private paths, raw provider bodies, or arbitrary EPUB
text. Failure output is fixed and value-free.

Report and current-pointer publication is one SQLite transaction. Immutable
report rows have update/delete triggers. The separate pointer may advance, but
predecessor reports remain unchanged.
