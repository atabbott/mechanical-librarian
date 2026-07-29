# Changelog

## Unreleased

- Preserve high-confidence work-scoped title and complete ordered creator
  agreement when original values differ only by one closed, explainable
  comparison form: exact bytes; NFC/case/whitespace normalization; mapped
  non-breaking spaces, paired curly quotes, and en/em dashes; or conservative
  single-author `Family, Given` inversion. Structurally parsed provider
  name-to-identity bindings may support aliases without replacing any source
  claim or creating another provider. Incomplete optional bindings remain
  field-local and cannot override direct complete-name comparison; conflicting
  complete bindings, reordered or duplicated names, role/scope incompatibility,
  diacritic stripping, fuzzy or subtitle-composed values, and marked
  variant/translation/abridgement values remain disagreement or non-comparable.
  Persist each truthful lexical, closed-field normalization, or
  structural identity-binding explanation in `metadata-report-v20` under
  comparison policy `closed-title-creator-v3`; v19 and earlier databases are
  rejected without mutation or migration. (#399)

- Preserve every independently valid, bounded Google Books, Hardcover, and
  Open Library claim or closed rich-evidence value from a safely anchored
  provider record when an optional sibling is absent, unsupported, malformed,
  oversized, or untyped. Valid collection members and repeated occurrences
  retain source order; an over-cap collection is rejected as one field without
  retaining a prefix. Required response shape, candidate identity and
  edition-to-work joins remain fail-closed. Provider-owned parser/schema
  rejections become one bounded, aggregate-safe diagnostic per rejected source
  occurrence and merge once with attempt diagnostics across persistence and
  reopen. Extend the closed rich-evidence vocabulary, work/edition scopes and
  controlled contributor roles, and bind reviewer evidence identities to the
  occurrence's pre-canonical `source_ordinal`. Advance the clean exact-current
  report contract to `metadata-report-v18`; deterministic replay preserves
  claims, rich evidence, duplicate occurrences, provenance, scopes, roles,
  states, raw/source order, source ordinals, and diagnostics. Earlier report
  databases are rejected without mutation or migration under ADR-0013. (#400)

- Coalesce semantically identical provider discovery lookups within one
  report-only observation. Each discovery lane retains its own provenance while
  replaying the immutable completed provider result, so coalescing does not
  create a cross-report cache or widen provider request authority. (#398)

- Expose one closed, aggregate-safe provider and reviewer failure taxonomy
  without copying exception text, credentials, paths, prompts, packets,
  provider bodies or records, process streams, headers, timings, digests, or
  attempt ordinals into public or durable status. Provider attempt evidence is
  mapped once at the application seam while `ProviderStatus` remains
  unchanged; Hardcover HTTP 200 GraphQL errors are terminal
  `provider_declared_error`, never success. Shadow failures persist the same
  canonical diagnostic object used by CLI failure output and offline restart
  inspection. The pinned reviewer now runs in JSONL mode and maps only its
  exact bounded terminal `codex_output_schema` rejection event to
  `live_schema_rejection`. After canonical outer shape and exact request-ID
  syntax are proven, a zero-exit result naming another request is a typed
  `identity` failure before citation checks; malformed identity remains
  semantic, other semantic/citation failures remain `citation_membership`, and
  repository faults at application and CLI-runner boundaries are
  typed `persistence`. Outcome-less dispatched attempts reopen with one bounded
  `unknown` reviewer diagnostic, and discovery-plan overflow records
  `lookup_construction` in each affected provider observation. Malformed
  optional provider attempt records fail closed to `unknown`; report replay
  admits only canonical provider diagnostic aggregates. Inspection binds both
  case and request identities, rejects duplicate or out-of-order aggregates,
  and untyped CLI runner exceptions map to `unknown`. Shadow failure record,
  durable replay, application inspection, and CLI inspection admit only the
  closed reviewer-owned category/component/stage shapes, including the exact
  persistence/persistence/persistence producer.
  Malformed output and every other nonzero exit remain separate. Advance clean
  exact-current contracts to
  `metadata-report-v18`, `shadow-review-inspect-v2`, and
  `shadow-review-failure-v2`; proposal, packet, request, and result bytes remain
  unchanged. Earlier development report databases are rejected without
  migration under ADR-0013. ADR-0017 freezes the privacy and replay boundary.
  (#381)

- Preserve provider publication evidence with the same closed event and
  year/month/day/timestamp precision vocabulary as local EPUB evidence.
  Provider claims retain lexical and normalized values plus explicit or
  unresolved event/manifestation linkage; Google Books publication strings
  remain visible but non-selectable. Proposal and reviewer candidates expose
  their event and target precision, while citations retain source precision,
  candidate/work identity, provider authority, query access, and sibling
  identifier/format fact context with exact bounded membership. Constructed
  and replayed reports, proposals, and reviewer packets reject source
  precision disagreement, destination/date disagreement, opaque-only linkage,
  malformed context, and context over its fixed limits. Identifier fact
  references are recomputed from exact public-safe provider/candidate
  membership and raw claim identity with ISBN/ASIN validation; cited typed
  claims and rich date evidence cannot omit linkage. Provider-linked packet
  and request replay now also requires the authoritative immutable report and
  rejects invented checksum-valid identifiers or physical formats even when
  all internal identities are recomputed; non-provider replay remains
  self-contained. Lowercase provider ASIN input is canonicalized to the closed
  uppercase form before its content identity is constructed, and date
  comparison maps aware timestamp operands to their exact UTC instant so
  equivalent nonzero offsets agree without discarding either source lexical
  value or precision; naive timestamps fail closed.
  ADR-0016 supersedes ADR-0015 to freeze the
  packet-v2/request-v3 trust boundary. The first actual provider dispatch now
  records
  one strict canonical UTC access time from the adapter's injected clock;
  retries and replay do not refresh it, and pre-egress outcomes retain an
  explicit absence. A fresh accessed observation is therefore a new immutable
  report successor even when its bibliographic facts are unchanged. Year and
  month evidence can derive only
  `publishedYear`; day and signed-offset timestamp evidence can derive an exact
  day without converting offsets; work-first dates cannot replace populated
  manifestation values; and conflicts remain unresolved choices. Advance the
  exact-current contracts to `metadata-report-v14`, `proposal-set-v2`,
  `shadow-review-packet-v2`, and `shadow-review-request-v3`; earlier
  development state is rejected without migration under ADR-0013. (#382)

- Admit source-explicit target-work subtitle, publisher, and imprint from the
  existing bounded rear EPUB indicia pass under the same structural-semantic or
  exact-label rules as front matter. Reports preserve publisher versus imprint,
  every repeated or conflicting occurrence, exact bounded excerpts, and closed
  rear resource/spine/tail/marker provenance through canonical serialization
  and database reopen. Copyright holders, printers, distributors, retailers,
  conversion, scanning, production, workflow, advertisements, previews, and
  other-work material remain excluded even when nested inside a field label.
  Each complete nested or sibling label owns its exact occurrence span without
  a synthetic combined value, while unmatched split labels in the same
  ancestor remain assembled. Repeated equal spans remain separate, including
  complete and split siblings with the same value. Front/rear canonical values
  share NFC and whitespace normalization. Rear
  position supplies no candidate authority or write capability. Advance
  material-recognition and citation compatibility from
  `epub-opening-v2` to `epub-opening-v3`; earlier opening citations are rejected
  instead of being reused. Advance the clean exact-current report and database
  discriminator to `metadata-report-v13`; earlier development reports and
  databases are rejected without migration under ADR-0013.
  (#377)

- Recover source-explicit contributor roles and selected-manifestation
  publication dates from bounded package, front, and rear EPUB indicia.
  Compound bibliographic roles remain attached to their contributor occurrence;
  production, conversion, software, retailer, scanning, and workflow credits
  remain contextual-only. Date events retain their exact
  year/month/day/timestamp precision and signed offsets without conflating work
  first publication, copyright, creation, modification, or conversion with the
  selected ebook. Advance clean exact-current opening, child, and report
  contracts to `epub-opening-report-v2`, `epub-inspection-v3`, and
  `metadata-report-v12`; earlier development state remains incompatible under
  ADR-0013. Unsigned date-times and mixed work-first/selected-edition wording
  now fail closed without lower-precision truncation or cue-order-dependent
  event guessing,
  adjacent explicit contributor credits remain independent, and durable local
  date-event scope must agree with the event. (#374)

- Recover from narrowly classified transient metadata-provider failures within
  provider-local fixed retry, pacing, byte, deadline, and dispatch bounds.
  Google Books permits at most 54 attempts, Hardcover 27, and Open Library 49;
  rate limits and provider-declared GraphQL errors remain fail-closed. Advance
  the exact foreground batch artifact to `batch-manifest-v5`; `config-v5`,
  `metadata-report-v11`, `fixed-conservative-v2`, and reviewer contracts remain
  unchanged. (#391)

- Restore current Hardcover ISBN and complete-title/author GraphQL operations
  within the provider's three-relationship-depth limit while preserving typed
  taxonomy through a bounded sibling-root join. HTTP-success responses that
  declare bounded GraphQL errors now yield `unavailable` with no candidates or
  cover descriptors; malformed or over-bound error envelopes remain
  `malformed`, and no provider diagnostic or credential enters retained
  evidence. Nullable or malformed Hardcover contribution roles and authors now
  remain field-local typed claims without discarding an otherwise valid
  candidate or being inferred as author identity evidence. (#378)

- Add Google Books as an ordinary report-only metadata provider with
  credential-free and optional controller-owned API-key modes, fixed bounded
  ISBN/work discovery, provider-local volume identities, explicit unresolved
  work linkage, evidence-only publication strings, typed transport/quota
  outcomes including unregistered daily-limit exhaustion, field-local malformed
  or oversized scalar claims, strict identifier-entry validation, one shared
  report deadline independent of cover support, deterministic replay, and
  end-to-end proposal/review evidence persistence.
  Advance the exact configuration and report contracts to
  `config-v5`, `metadata-report-v11`, and `batch-manifest-v4`; earlier
  development state remains clean-schema incompatible. (#315)

- Keep a controlled package-metadata `Unknown` creator visible in report
  evidence while preventing it from becoming an authors replacement by itself.
  Mixed real creators retain their order, and legitimate anonymous, nonexact,
  provider, and opening-matter names remain eligible. Advance the closed
  proposal policy to `fixed-conservative-v2` and the exact proposal-store
  envelope; earlier proposal policy documents and stores are rejected without
  migration or conversion. (#376)

- Accept order-insensitive, repeated known reviewer citations at the untrusted
  output boundary, then persist and replay one deterministic sorted unique set.
  Unknown, missing, malformed, mixed-validity, and over-bound citations remain
  invalid; exact raw bytes stay private and only an aggregate typed
  normalization marker crosses the adapter seam. (#379)

- Keep EPUB ISBN recognition occurrence-local and reject checksum-valid
  ISBN-shaped values explicitly owned by a non-ISBN package scheme or URI.
  (#372)
- Search every distinct checksum-valid local ISBN-10/13 family through its own
  bounded provider lane, alongside one complete-title/author work lane when
  available. Reports retain exact triggers, lane outcomes, provider-local
  convergence, conflicts, and typed no-subset overflow without ranking,
  selection, retry, or write authority. Advance the clean exact-current report
  contract to `metadata-report-v10` and the truthful foreground request maxima
  to `batch-manifest-v3`; earlier report databases and manifests are rejected
  without migration or interpretation. (#373)

- Harden the isolated qualification reviewer schema: typed bound constraints,
  pinned subset validation and request/digest binding, a real one-case
  empty-job canary through the production execution gate, and durable
  malformed-document versus semantic-validation failures. Advance the
  exact-current report/store contract to `metadata-report-v9` and the bounded
  request envelope to `shadow-review-request-v2`; earlier development state is
  rejected without migration under ADR-0013. Nonzero reviewer exits remain
  conservative `runtime` failures unless a future pinned canonical signal can
  prove the narrower reserved `response_format` cause. (#380)

- Correct historical documentation attribution: the 0.4 checkpoint had one
  `metadata-report` command and a two-table report store; later 0.5 retrieval,
  richer evidence, and cover-artifact additions remain current-runtime features.
  The 0.9 checkpoint remains evidence-and-ProposalSet-only; review admission
  and shadow advice belong to 0.10. (#370)
- Accept the pinned Codex CLI's exact canonical ChatGPT subscription status on
  either process stream only when the other stream is empty and the command
  succeeds. Mixed, additional, malformed, API-key, and nonzero results remain
  rejected. (#368)
- Isolate qualification reviewer authentication under the exact pinned
  Bubblewrap artifact with an immutable empty `config.toml` data bind. Auth now
  uses only supported Codex CLI command scopes; it adds no model invocation or
  normal activation authority. (#365)
- Add the qualification-only isolated Codex shadow-review path for one exact
  current ReviewCase. It freezes the executable/model/reasoning/prompt/profile/
  packet/output/policy tuple, supports explicit ChatGPT subscription auth,
  no-egress packet preview, one foreground request, offline inspection, and
  explicit reevaluation only after prior process-tree death. Append-only
  request/attempt/dispatch/outcome facts retain only sanitized canonical data
  and bounded diagnostics; no decision, apply, write, normal activation,
  background work, automatic retry, runtime install, or fallback is added.
- Add exact-current atomic review admission with immutable undecided cases,
  typed no-case retirement, derived currentness, and clean-schema-only SQLite
  lineage pointers and transitions.

- Preserve safe Audiobookshelf target values that exceed their existing field
  bounds as `oversized` without letting size hide invalid syntax or unsafe
  content. Non-title failures remain `malformed`; unsafe string titles reject
  the target response, while other unusable title shapes retain their
  field-local state without widening report limits.
- Advance the exact-current report/database contract to `metadata-report-v8`.
  Existing `metadata-report-v7` databases are rejected under the pre-release
  clean-schema policy; no migration, import, or conversion is provided.
- Preserve malformed present Open Library cover IDs as the bounded report
  terminal state `rejected`, distinct from genuinely absent cover IDs; no cover
  request or write is attempted.
- Preserve valid Open Library metadata when optional cover provenance context
  exceeds its fixed bound; retain the bounded cover terminal state as
  `oversized` without widening candidate or cover limits.
- Fail closed during no-ISBN work fallback when a candidate title or subtitle
  identifies an abridgement or abridgment.
- Activate immutable explainable ebook proposals. `proposal-set-create`
  revalidates one exact current `metadata-report-v8` target, media snapshot,
  ABS cover, and private artifact authority, then evaluates a fixed
  conservative policy into exactly one closed result for each of the 16
  Audiobookshelf destinations without provider calls. `proposal-set-get`
  reopens the exact canonical `proposal-set-v1` bytes. Proposal publication
  rechecks the report digest and current pointer inside the insertion
  transaction, remains append-only, and adds no review, decision, or write
  authority.
- Expand the immutable ebook evidence report to `metadata-report-v7`: preserve
  all 16 Audiobookshelf metadata destinations and their exact loading/value
  states in target currentness, use one exact title-plus-complete-author work
  lookup only when no usable ISBN exists, retain typed Hardcover taxonomy and
  same-work first-publication evidence, and keep fallback work evidence from
  acquiring edition authority.
- Expand the report-only cover gallery to at most eight provider sources and
  four per provider. Each admitted source retains safe provider/work/edition
  provenance and available edition context; content-addressed duplicate bytes
  preserve every provenance row, and provider/gallery truncation is explicit.
- Make public installed-artifact qualification exact-source: build with caches
  disabled from a clean bound revision, validate its archive and `RECORD`, seed
  a new environment from locked dependencies, install the explicit wheel
  without resolution, run the full suite against it, prove wheel-to-install
  payload identity and origin, and run its command outside the checkout.
- Add independent, report-only Open Library evidence for an exact usable ISBN
  family alongside Hardcover. The built-in adapter uses canonical exact
  Search/edition/work requests, a fixed one-request-per-second pace, at most two
  candidates and four covers (two editions plus two distinct works), and a
  closed finite rich-evidence allowlist.
- Add adapter-specific `config-v4`, `metadata-report-v6`, and
  `batch-manifest-v2` contracts. Open Library's non-secret operational contact
  is used only in identified requests and excluded from persisted data and
  fingerprints.
- Limit Open Library foreground batches to at most five explicit entries before
  manifest creation or egress, while preserving larger and all-eligible batches
  when that adapter is disabled.

- Add a synthetic, aggregate-only EPUB evidence recoverability audit contract.

- Add the read-only current Audiobookshelf book catalogue with bounded refresh,
  deterministic inspection, and config-v3 schema.
- Correct catalogue refresh retention, empty plural ebook classification,
  path-only EPUB mapping, and safe absent/exact inspection failures.
- Add report-only foreground catalogue evidence-batch preview, immutable
  manifest inspection, and serial execution for explicitly selected or eligible
  EPUB entries.

- Add report-only, source-attributed ABS, EPUB-declared, and bounded Hardcover
  cover evidence. Valid JPEG, PNG, and static WebP inputs produce private
  content-addressed originals and deterministic offline preview artifacts; no
  cover selection or write capability is added.

- Expand the bounded configured Hardcover observation to persist ordered rich
  edition/work field evidence, roles, source scope, and per-local comparisons.
- Preserve each bounded Hardcover candidate's opaque provider-local work
  identity alongside its distinct edition identity.
- Classify every retained ISBN claim as accepted, associated, unrelated,
  invalid, or unresolved using primary-package, separately provenanced
  package/opening, and provider-local work evidence. Secondary and conflicting
  identifiers remain typed and report-only, and exact replay validates the
  derived relationship projection.
- Keep ISBN acceptance claim-local: only explicitly designated package
  primaries are projected as ebook identifiers, while inferred primaries,
  print labels, ambiguous labels, and conflicts remain unresolved unless
  bounded provider-local evidence proves association.
- Keep provider-work ISBN collisions unresolved: an accepted ISBN family is a
  work-association anchor only when it maps to exactly one work within that
  provider, without weakening independent provider evidence.

- Preserve source-spine slot positions for bounded local EPUB rear evidence and
  make exact-ID report reopening non-creating.
- Preserve validated package and opening evidence when the optional bounded
  rear EPUB pass reaches its aggregate resource limit, and expose that bounded
  disposition in the retained report.
- Preserve local EPUB source/DOM evidence order, explicit source capabilities,
  and non-canonical oversized or unsafe sentinel states through report replay.
- Preserve duplicate EPUB occurrences with bounded v2 ordinals.
- Exclude package `bkp` book-producer credits from bibliographic contributors
  while preserving raw package evidence. Retain only the explicit
  `Book|Volume N[.N] of|in Series Name` opening-matter cue; an unqualified
  trailing series/order form remains absent.
- Admit standards-valid signed-offset publication date-times from structured
  EPUB package metadata while preserving lexical values and event typing.
- Preserve explicit opening-matter publisher and imprint evidence as distinct
  edition roles through inspection, immutable reports, replay, and comparison.
- Require every 0.5 increment to accept only an absent database or its exact
  current schema. Prior and intermediate schemas are rejected without mutation;
  same-schema exact-ID/current-scope replay remains available, and recovery is a
  clean database plus supported report regeneration.

- Added `metadata-report-v2` local-field evidence: complete ordered field
  states, package/opening evidence, and bounded rear-spine copyright evidence.
  Exact persisted reports can be reopened by report ID without operational
  configuration, credentials, target access, or network access.

- Activated the clean-state 0.4 neutral metadata-evidence runtime. At the 0.4
  boundary, the installed package exposed only `metadata-report` with required
  config, database, connection, library, item, and file-index inputs.
- Added explicit named target connections, libraries, read-only mounts, and
  provider connections with per-library provider references and explicit
  enablement.
- Added scoped current-item Audiobookshelf reading, the isolated EPUB inspector,
  one fresh bounded Hardcover request per applicable provider, neutral
  all-candidate title/ISBN claims, exact canonical replay, final target/media
  currentness, and an append-only two-table SQLite report baseline.
- Removed the pre-0.4 inventory, workflow, job, preview, proposal, review,
  retry, cache, migration, and compatibility runtimes and their installed
  commands, schema, fixtures, scripts, and operational documentation.

No tag, release, deployment, or production write authority is created by this
development checkpoint.
