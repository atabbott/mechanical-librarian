# Changelog

- A sole confirmed ebook cover remains available for owner review when sibling
  acquisition times out. Automatic cover replacement distinguishes a known
  empty target from an unverified incumbent and replaces a confirmed incumbent
  only with a strict rank upgrade. Existing installs require a clean rebuild.
  (#999)

## Unreleased

- Fill empty ebook ISBNs from verified retained edition evidence even when its
  associated image is unavailable. Representation, owner choices and write
  safeguards remain unchanged. Clean rebuild required (#1077).

- Show admitted ISBN choices when the owner selects Ask (#1077).

- Qualification maintenance separates current automatic authority from evidence
  provenance while preserving historical decisions. Earlier derived stores require
  a clean rebuild; automatic grants and write safeguards are unchanged (#1102).

- Fill empty ebook descriptions from exact eligible same-work synopsis text,
  including concise alternatives, while excluding catalogue placeholders from
  automatic choices. Ask supports explicit owner choices and current views
  explain refusals. Clean rebuild required (#1076).

- Audiobook synopsis policies preserve useful existing prose, fill empty values,
  and optionally replace recognized chapter/track or no-synopsis placeholders
  with an original supported source description. Old derived state requires a
  clean rebuild. Manual field selection keeps keyboard focus in the Library (#1070).


- Supported first-book series may share the preserved book title when filling
  empty series, while sequence, identity and owner controls remain enforced.
  Proposal policy v31 requires a clean rebuild; report v49 is unchanged (#1075).


- Accepted author display inversion no longer blocks an eligible automatic genre
  fill. Ask exposes eligible genre choices; Ignore and owner-final choices remain
  authoritative. Genuine genre conflicts still abstain. Earlier application state
  requires a clean rebuild. (#1074)

- Equivalent English names and language codes no longer create false choices.
  Undetermined language remains readable evidence without automatic authority.
  Language preferences select an observed form and preserve equivalent current
  values; genuine language and script conflicts still need review.
  Earlier application state requires a clean rebuild. (#1071)

- Restart preserves completed preparation records when library ordering is
  reconciled, so unchanged completed work remains readable. (#1073)

- Supported unchanged titles record one durable no-change without disabling
  Automatic title decisions. Explicit title recovery re-evaluates eligible
  completed books after all blocking safety controls are explicitly cleared,
  using retained evidence without writing library metadata.
  Earlier application state requires a clean rebuild. (#1073)

- Failed preparation stays visibly separate from completed work and points
  owners to the existing retry action after temporary failures. (#1084)

- Malformed provider titles no longer stop preparation of usable sibling evidence.
  Rejected title text stays out of reports and choices; safe Unicode is preserved.
  Report v47 requires a clean rebuild of prior development state. (#1084)

- Authors routed to Ask remain available for owner review and choice. (#1083)

- Photo credits and incidental EPUB text no longer replace book authors.
  Complete corroborated author sets can qualify without a local byline;
  competing authors remain for owner review. Report v46 requires a clean rebuild.
  Automatic decisions still require separate consent and verification to apply. (#1083)

- Preserve multiline current Audiobookshelf descriptions in ebook and audiobook
  review, including exact paragraph breaks and boundary whitespace (#1080).
  Report v45 requires a clean rebuild of prior development state; comparison
  equivalence never relaxes exact write preconditions or read-back verification.
  Activity distinguishes unconfirmed writes from closed preparations.

- Preparation keeps ownership across committed stages and reports a stopped
  runtime when failure settlement cannot finish. Ready books remain browsable;
  an ordinary service restart resumes retained checkpoints. Linux startup safely
  admits supported interrupted private database transactions. (#1085)

- Owner navigation, choices and Settings take priority over background database
  work; collections show truthful loading and retry states. Choices return their
  actual recorded confirmation without a competing background read. Existing
  application databases require a clean rebuild; currentness and guarded-write
  protections remain unchanged. (#1072)

- Managed dependency upgrades, including majors, request automatic merge after
  all five required checks. npm and OSV join managed scope; frontend dependency
  bytes retain build provenance without rotating semantic policy qualification.
  New dependencies and compatibility-policy changes remain manual. (#1089)

- Reviewer settings now name the cover reviewer's configured model beside its
  engine, and say plainly when contained Codex reports none. The cover task
  states what a cover model must support and the largest request it can send,
  and says that no price, price date or per-request figure is observed for it,
  so nothing is presented as the cheapest. (#1009)

- Ebook preparation no longer fails when an audiobook provider returns multiple
  matching recordings. Recording fallback candidates remain audiobook-only. (#1065)

- Preparation now states one run's ready, failed and in-progress counts, each
  stage's own numerator and denominator, and a finishing rate only while the run
  is moving. Every completed book links into Works, and a book that stopped
  before a report says what it is listed as instead. The background-work control
  names each scan state, counts what it checked, explains what Scan now and Retry
  do, and closes on Escape. (#1012)

- First-run setup lists what the deployment must supply, states the owner-password
  rules before submission with an accessible reveal control, and shows each refusal
  on the field it belongs to in its own words. Connection, origin, mount and kickoff
  failures give a safe cause and one remedy, keep entered service paths, reopen only
  what the deployment accepted, name each library once, and stay readable at narrow
  widths and enlarged text. (#1008)

- The first-run lifecycle qualifier again names the setup journey test it runs. (#1008)

- Qualified audiobook metadata now uses supported work and recording evidence
  for useful field decisions, with complete casts, tags and original dates.
  Title/subtitle pairs apply together; explicit flags only set true, and covers
  replace only known undersized incumbents with materially larger alternatives.
  No-change and source refusals remain visible; application stays default-off.
  Existing installs require a clean rebuild. (#1047)

- Qualified audiobook Automatic choices cover title, authors, narrators,
  language, series and genres, with complete credits and explicit list operations.
  Recording-bound subtitles remain available for owner review;
  unknown identifiers, subtitle/tags and cover Automatic stay withheld.
  Policy shows all sampled outcomes and wraps enlarged guidance; malformed taxonomy retains valid fields.
  Expanded descriptions keep Evidence clear; state requires a clean rebuild. (#967)

- Complete audiobook chapters can support owner review when overall runtime is
  unavailable, including target-asserted and WORK-derived recording lookups.
  Conflicting runtimes and different chapter cuts remain unqualified; Library
  and Detail identify the supporting source. Automatic authority is unchanged.
  Existing installs require a clean rebuild. (#1004)

- Audiobooks with one local ASIN can reach owner review when a provider omits
  book runtime but complete chapter timing and structure corroborate the recording.
  Library and Detail explain the source; partial chapters remain readable without
  qualifying this fallback. Automatic authority is unchanged. Existing installs
  require a clean rebuild. (#1042)

- Ebook titles supported by retained ISBN and creator evidence can be corrected
  independently of subtitle or series choices. Automatic decisions remain
  visible and reversible by the owner; Ask and Ignore remain authoritative.
  Failed workspace loads can be retried without reloading the browser page.
  Existing application state requires a clean rebuild. (#1007)

- An audiobook's media-health section now states what each file's own headers
  hold: a container that is not the one its file type names, a stream outside
  the expected codecs, headers that state no running time, and Apple-protected
  samples. A recording short of the edition it was matched to is stated for
  every lane, including those whose provider runtime publication does not
  retain. Existing installs require a clean rebuild. (#144)

- An audiobook decision class can now earn automatic authority through its own
  qualification record, one tranche at a time, and no ebook record ever speaks
  for a recording. No record is published yet, so every audiobook proposal is
  still the owner's and nothing automatic acts on one. Routing `asin` to
  Automatic becomes possible only once a record qualifies it; an ebook's `asin`
  is unchanged. (#967)

- An audiobook's evidence view, its review case and the rows a stopped
  preparation leaves behind now state a closed set of typed media-health
  observations, each naming the track or provider evidence it was read from and
  the bound it violated. A zero-byte file now ends the item `unreadable_media`
  rather than `parser_refused`. Current rich-metadata qualification moves to F20
  v24 and F19a v10, rebinding the changed sources without rescoring. (#144)

- Configure optional description advice independently of cover review, with
  editable cleanup preferences and automatic application off by default.
  Authored text keeps its advised provenance through review, guarded writes,
  reversal and history. Existing application state requires a clean rebuild
  (#892).

- A provider that restates one candidate identity with claims that disagree now
  fails closed as an ambiguous identity rather than a malformed response, and a
  provider cover whose work is unresolved is used only when its edition is one
  that provider actually observed. Nothing is fetched for a cover that names a
  different or no edition. Existing installs require a clean
  `metadata-report-v39` database. (#957)

- Add an exact-candidate local Linux fast check and an opt-in per-test
  deadlock deadline, with conditional platform-sensitive qualification (#777).

- Settings now carries a Cover evidence switch for Audible and iTunes, off while
  the provider is off and unchanged for every other source. Setup, Settings and
  the review surfaces state one canonical name per provider, with the instance
  name shown beside it. The audiobook runtime corroboration gate owns its own
  90-second band instead of borrowing the report's comparison band. (#967)

- Restore manual mutant generation, test-selection statistics, and audit execution
  with the pinned mutation engine (#901).

- Correct idle-load qualification to measure an exact 42-minute window and preparation cadence (#917).

- Exact-artifact qualification supports mise's `uv` shim without a PATH
  workaround (#924).

- Automatic rich-metadata decisions now bind the local-transport qualification.
  Earlier qualification decisions remain readable but cannot authorize new
  effects; qualified options and scoring are unchanged (#844).

- Preparation totals retain completed libraries while another library finishes,
  keeping multi-library workspaces valid across restart (#844).

- Rediscovering unchanged completed work keeps preparation valid for Activity
  and restart without repeating inspection (#844).

- Restarting an idle workspace preserves its application database bytes by
  avoiding an empty preparation cleanup write (#844).

- Connect to HTTP Audiobookshelf origins and use the owner workspace over local
  HTTP, trusted direct HTTPS, or an exact HTTPS proxy. Fresh deployment defaults
  to loopback HTTP. Replace configuration/bootstrap documents with config-v9
  and review-bootstrap-v2; database schemas are unchanged (#844).

- Clarify current publication candidate sufficiency and its separate owner-action and automatic-authority limits (#1003).

- Library explains provider evidence groups, lookup states, and field-local automatic withholding (#891).

- Audiobook decisions now apply through the existing guarded Audiobookshelf path,
  with narrators joining the write-capable destinations on both media. Because
  the consent envelope covers the whole destination set, every library consent
  lapses on upgrade and Activity re-offers it before anything is scheduled.
  Decisions recorded before audiobook writes existed stay readable history and
  never apply. (#968)

- Inventaire cover evidence now survives preparation. Valid missing ISBNs permit
  bounded title-and-author fallback, and three-row searches retain useful work
  evidence without concealing ambiguity or incomplete author identities.
  Corrected failures can be deliberately prepared again (#1006).

- Qualified title decoration tidies retain independently supported subtitle and
  series components in complete review choices; existing evidence and automatic
  authority guards remain in force. Proposal policy v23 requires a clean rebuild
  of earlier state. Owner review counts only recorded outcomes (#1002).

- Cover comparisons keep the exact Audiobookshelf incumbent artifact and
  dimensions when another source has the same preview bytes. Library attention
  now follows live provider recovery, and a successful replacement after a
  stale comparison clears that comparison's recovery notice. (#999)

- Equivalent local description evidence no longer replaces an eligible
  provider's exact text or makes a qualified automatic fill disappear. Source
  provenance and owner choices remain visible, and existing installs require a
  clean `fixed-conservative-v21` rebuild. (#1001)

- An ISBN hit about a different work no longer suppresses bounded title-and-author
  discovery or lends publication-date or automatic-genre authority. The retained
  evidence stays inspectable, including cases where exact work identity cannot be
  proved. Existing installs require a clean `metadata-report-v38` database and
  `fixed-conservative-v20` proposal state. (#1000)

- Automatic series fill refuses unproved work identity, including mismatched
  ISBN results, while accepting source-attested title decorations, exact
  edition titles and conservative single-author comma inversion. Ask choices
  and surviving values retain their original bundle and provenance. (#895)

- Library completion and recorded summaries retain the owner's current value,
  including an empty value, after overriding an automatic candidate. (#895)

- Qualified series fill preserves populated Automatic fields, accepts
  absent numbers without inventing them, and selects one uniquely compatible
  source bundle instead of combining memberships. Failure checks follow its
  actual evidence; Library explains editable automatic outcomes and number
  certainty. V13 binds the repair through exposed regression; historical v11/v12
  decisions remain readable and guarded writes require current authority. (#895)

- Preparation no longer stalls when a recovery wake opens the database while
  another connection is writing: same-process dependent opens borrow the live
  root's admission on every platform, schema validation reads one snapshot, and
  a failed recovery wake leaves preparation lane 0 running. Offline opens still
  refuse leftover journals. (#992)

- A series, genres, tags, description, or subtitle pass that finds the target
  already holding the value it would keep now records that no-change fact
  instead of failing authority binding. One such decision no longer stalls
  continuous application for the whole install: a decision that cannot bind is
  named in Activity and stepped over, leaving every later automatic and human
  decision free to apply. (#993)

- Automatic series decisions now accept one complete provider authority while
  retaining closed abstention for named incomplete evidence, genuine ambiguity,
  or a contributing provider-lane failure. Unsupported provider-wide series
  output is no evidence; only closed display variants collapse. Replays are
  qualified at 100% precision; fresh mixed confirmation remains required before
  activation. (#977)

- Audiobooks whose work identity is admitted now produce review cases: narrators,
  ASIN, series, description, language, title and authors are decided through the
  same rules an ebook uses. Subtitle stays withheld for a recording until
  audiobook subtitles are typed as bibliographic. Existing installs require a
  clean rebuild under `proposal-set-v6` and `human-decision-v2`. (#966)

- A prepared audiobook that produces no case now completes on a recorded outcome
  instead of a failure code, and the library counts it as recorded. (#940)

- Review now keeps unavailable, unproposed primary fields out of the default
  rail while offering a one-way reveal for inspection. (#890)

- WORK replay now admits one exact closed-equal complete author multiset when
  provider ordering alone differs; missing, extra, duplicate-replacement, and
  transformed author matches still reject. (#889)

- Latin titles and complete ordered author names differing only by closed
  canonical combining marks can identify one provider work; original claims stay
  visible and non-Latin or compatibility substitutions reject. Existing installs
  require a clean `metadata-report-v37` database. (#888)

- Titles that differ only by one closed decoration no longer compete: the plain
  form is canonical, an item already holding it is kept without a write, and one
  holding the decorated form is tidied automatically. A colon segment from
  another source now corroborates a subtitle, and a filename title carrying the
  author prefix plus underscores is admitted as one work. Existing installs
  require a clean `metadata-report-v36` database. (#896)

- Empty descriptions with competing provider candidates now select one exact
  target-work-applicable, quality-qualified value by a fixed provider order;
  unsafe or unresolved candidates remain visible for review. (#886)

- Empty ISBNs now fill only when deterministic identity evidence binds the selected
  provider ISBN to the file's work; unverified or competing families remain visible
  for review. Existing pre-release proposal state requires a clean rebuild under
  `fixed-conservative-v17`. (#897)

- Equivalent local author or narrator forms now adopt an exact provider-consensus
  display only when independent providers agree; the original local form remains
  in the decision evidence. Existing pre-release proposal state requires a clean
  rebuild under `fixed-conservative-v15`. (#887)

- Audiobook items whose file names carry characters the report cannot hold
  are now prepared normally: the track manifest records a projection of the
  name, so an item is no longer lost to an untyped failure over a value
  nothing is decided from. (#975)

- Audiobook items made of several audio files are now prepared as one item:
  every file is read under its own bounded child, and composed into one running
  time, one chapter list, and one claim per observation carrying the files that
  stated it. Files that disagree keep both values, a refusing file names its
  track, and an item too wide to compose is listed for attention. Existing
  installs require a clean `metadata-report-v35` database. (#965)

- Audiobook preparation now feeds the Audiobookshelf-asserted ASIN and any
  ASIN the runtime-corroboration gate promotes into the per-ASIN providers in
  the same pass, after embedded ASINs and within the same four-lane bound;
  candidates reached that way keep edition identity only when the recording's
  own running time corroborates them. Existing installs require a clean
  `metadata-report-v34` database. (#956)

- Provider tag proposals now require exact retained work identity before they
  can be elected automatically. Conflicting or incomplete provider work
  evidence remains available for inspection. Earlier automatic tag decisions
  remain readable history but no longer authorize an effect. (#964)

- Language aliases and regional variants now form one decision alternative,
  while different base languages, script variants, and malformed tags remain
  unresolved. Existing pre-release proposal state requires a clean rebuild
  under `fixed-conservative-v13`. (#885)

- When independent providers agree byte-for-byte on a clean work title, one
  reversible local decoration can advance it for automatic review; ambiguous,
  dissenting, and uncorroborated or unpreserved colon-subtitle cases remain
  owner decisions. Existing pre-release ProposalSets require a clean rebuild
  under `fixed-conservative-v14`.
  (#884)

- Inventaire is available as a keyless ebook metadata and cover provider in
  Settings and first-run setup. It resolves edition claims, linked works and
  English/fallback Wikidata labels through bounded fixed-host requests. Existing
  installs require `config-v8`, `owner-settings-v3`, `setup-state-v3`, and a
  clean `metadata-report-v33` database. (#883)

- Under provider quota pressure, exact-current recovery spends requests first
  on missing corroboration, then zero evidence, then settleable disagreement,
  before deterministic blanket scanning; provider-wide cohorts stay restart-safe
  and fair to later work. Existing pre-release recovery state requires a clean
  rebuild. (#882)

- Ebook providers try eligible usable ISBNs before title/author discovery and skip
  the latter only when an identifier yields candidates with no recoverable sibling;
  all other outcomes keep discovery, with accounting exposing executed lane kinds. (#881)

- The hosted `container-image` check now extracts the pinned `ffprobe` from
  the image it builds, installs it in place of the runner's own, and executes
  the real-probe tests against it — audiobook inspection evidence is remote
  qualification rather than maintainer-local practice. (#929)

- Refresh the completed work's Library presentation before terminal preparation
  settlement, so an orderly completed-state restart accepts its exact durable
  cohort without repair. (#954)

- Capture an explicitly authorized bounded private provider cohort before
  parsing and replay it offline through current adapters into fresh current
  reports, proposals, and review admission. #938 remains the complete-library
  qualification fallback; ordinary startup and upgrades are unchanged. (#939)

- Restore an explicit offline maintainer bridge for the retained v26
  qualification checkpoint. It verifies a clone and backup, rebuilds fresh
  exact-current reports through current derivation, and invalidates evidence
  missing current provenance; ordinary startup and user upgrades stay
  absent-or-exact-current. (#938)

- Audiobooks now consult four keyless providers — Audible, Audnexus, AudioSilo
  and iTunes — for read-only discovery, enrichment and corroboration, each
  addable from Settings and the first-run wizard. An ASIN is adopted only where
  a second authority agrees, covers corroborate by perceptual hash, and a
  report records every sidecar it read so an edited one makes it stale. Needs a
  clean database (`metadata-report-v32`) and a `config-v7` file. (#749)

- The container-image qualification job restores unchanged Docker layers from
  the repository Actions cache instead of rebuilding every stage per run; base
  images stay governed by the Dockerfile's digest pins. (#930)

- Tighten what an audiobook's local evidence may say: a wrongly shaped ASIN is
  reported as malformed rather than believed, a sidecar that is not valid UTF-8
  is dropped whole rather than repaired, an OPF contributor is narrator
  evidence only when its role says so, and a freeform MP4 atom is credited to
  Tone only when it names Tone. Evidence already published keeps its pre-gate
  claims until its item is prepared again. (#935)

- Remove the dead owner-decision-actions predecessor validator and its frozen
  schema constants from the persistence adapter; #872/#873 retired the last
  adoption plan that reached them and no caller remained. (#933)

- A prepared audiobook no longer settles its own scan: absences reconcile again
  in mixed-format libraries and nothing counts it as blocked. Its recorded
  evidence is reachable from the library row, its digest is bounded by a
  separate `audio_source_bytes` setting instead of the EPUB archive limit, and
  neither playback nor a later scan re-reads an unchanged file. Existing
  configuration files need no change. (#936)

- Read local evidence from single-file audiobooks in a mounted library — ASIN,
  narrator, series, running time, performance type, language, description,
  chapters, embedded-cover presence — each attributed to the tag, sidecar, or
  path it came from. Media is parsed only by an isolated `ffprobe` child built
  from checksum-pinned source with networking compiled out; no provider is
  contacted. Pre-release v30 state requires a clean rebuild. (#749)

- Carry one truthful preparation run from final setup into Library: accepted
  owner triggers, cumulative stage passage, bounded current names and recent
  ready/failed outcomes, reconnect state, and guarded terminal dismissal now
  share one server-owned projection. Restart recovery binds retained display
  high water to its exact authoritative work cycle. Existing pre-release state
  requires a clean rebuild. (#869)

- Record the audiobook provider posture in ADR-0061: keyless read-side
  providers with the anonymous Audible catalog primary, Audnexus per-ASIN
  enrichment, community tiers as degraded mode, marketplace-scoped ASINs, and
  no bundled dataset. A decision record only — no runtime change. (#925)

- Let the owner optionally select one exact cover-observation reviewer: staged
  Codex device sign-in or a write-only OpenRouter key/model, with immediate
  no-fallback rechecks and engine/model provenance. Earlier pre-release state
  requires a clean rebuild; the obsolete schema-adoption command is retired.
  (#872, #873)

- Show closed, observation-only cover defect checks before quieter style and
  advisory notes. Missing or failed reviewer evidence is labelled unavailable,
  never clean, and none of the new evidence changes match, recommendation, or
  automatic-selection authority. Exact v1/v2 reports stay replayable but appear
  unavailable until complete current evidence is recorded. (#865)

- Owner sign-in no longer waits on library-scan writes: session and login
  throttle state moved to a dedicated `private/review-sessions.sqlite`
  database with its own connection, the application schema dropped both
  tables, and `review-session-decoupling-v1` adopts the exact retained
  predecessor offline without a rebuild. (#912)

- Recommend the best confirmed cover deterministically without requiring the
  optional reviewer, and keep unconfirmed matches out of selection. Active
  provider fetching delays choice; retry waiting does not. A qualified
  default-off Automatic option is available after all frozen cohorts passed.
  Existing pre-release v29 state requires a clean rebuild. (#864)

- Ship the container on the exact Python and uv the public gate qualified, and
  keep it there: every toolchain pin site must agree, the qualified
  interpreter and resolver move as one Renovate group, and a half-landed
  update is a red check instead of silent drift. (#911)

- Report unittest-equivalent tier accounting for three pytest-native shapes:
  a function-scoped fixture failure is one error per case rather than one
  class-level error, `SkipTest` in `setUpClass` runs no tests and skips once,
  and a `load_tests` case pytest cannot collect is run instead of dropped.
  (#701)

- Shape work-only provider searches with a bounded query ladder and one
  provider-specific narrowing retry while retaining exact dispatch lineage.
  Query decoration never creates work identity; the reviewed admission subset
  is empty. Pre-release v27 state requires a clean rebuild. (#870)

- Pace Google Books, Hardcover, and Open Library request starts across
  foreground and recovery work. Exact burst throttling now waits briefly,
  daily exhaustion resumes at reset, and either deferral spends no recovery
  attempt. Pre-release v28 state requires a clean rebuild. (#876)

- Keep valid Hardcover discovery candidates when optional cover descriptors
  conflict across lanes; the conflicting cover remains fail-closed. Provider
  lane diagnostics now persist their bounded rejection category; existing
  pre-release `metadata-report-v26` state requires a clean rebuild. (#871)

- Remember the owner login for up to 30 days with a fixed, server-enforced
  expiry and verifier-bound session lookup. Logout and owner-verifier
  replacement end remembered authority. (#862)
- Upgrading to 0.18.0 signs out existing owners once: earlier sessions used an
  unbound digest and cannot be retained while preserving verifier replacement
  invalidation. Sign in again after the upgrade. (#862)

- Keep Activity reads out of the guarded background-operation gate and derive
  ordinary page totals from the same bounded activity projection as their rows.
  Live updates stay immediate during owner input, bound preparation reads in an
  untouched visible tab, remain quiet while hidden, and restore the server
  snapshot once when the owner returns. (#853)

- Keep deep Library and Attention pages reachable with the hardened 64 MiB
  temporary filesystem by sorting thin browse keys before loading page documents.
  Support exports retain only a bounded SQLite exception class if storage is
  unavailable. (#878)

- Keep repeated contained cover reviews within the exact process limit of their
  private user namespace, independent of application or sibling-container task
  counts. The supported container reaps successful and killed sandbox
  descendants before they can degrade later work. (#866)

- Reject stale contained-reviewer resource profiles at startup with a bounded
  manual-comparison reason, instead of allowing every cover review to fail.
  Operators regenerate profiles explicitly when the fixed contract changes. (#867)

- Qualify locally corroborated subtitle fill for automatic decisions:
  an empty Subtitle now fills automatically only when the book's own packaged
  metadata attests the exact provider value, including electing that value over
  competing catalogue splits. Existing pre-release proposal state requires a
  clean rebuild under the revised `fixed-conservative-v12` contract. (#744)

- Remove the dead review field-facts predecessor validator and its stale frozen
  schema digest from the persistence adapter; #830's owner-decision-actions
  predecessor validator superseded it and no adoption path referenced it. (#857)

- Add first-visitor owner-password setup and authenticated Settings for
  connections, providers, book libraries, limits, reviewer, server, and
  appearance. Changes activate atomically on the same listener. Disabling a
  provider retains its write-only value, removes its library associations, and
  leaves reassociation explicit; file/deployment authority stays locked.
  (#745, #762, #763, #764)

- Restore readable per-work Evidence and History links from Library. Evidence
  groups retained source values by field; History shows changed values or one
  retained value. Diagnostics retain exact Back state and expose no controls,
  raw facts, or internal identifiers.
  (#829)

- Make Library offer owner choices only when an eligible alternative exists, and
  wait for the exact acknowledged result before advancing. #830 adopts only its
  exact predecessor through the offline owner-actionability plan; other shapes
  require a clean rebuild. (#830)

- Keep authenticated preparation status available while local preparation writes
  hold SQLite's DELETE journal, without weakening recovery-sidecar rejection.
  (#828)

- Explain terminal preparation failures safely in Library and authenticated
  operational evidence. (#831)

- Refresh Library and Attention immediately when automatic capability suspension
  changes current review work. Scheduler, direct apply, reconciliation, and
  readiness now publish the same ordered Activity then Library update. (#794)
- Keep each preparation cohort's persisted discovery order across every
  eligible stage, so early works reach Library without waiting for a stage
  drain; attention rows with complete provider evidence now precede currently
  provider-limited rows without hiding either. Existing pre-release review
  state requires a clean rebuild because its claim index changed. (#826, #827)

- Keep one retained Activity event with unavailable automatic authority from
  taking down the ledger. The row explains its bounded limitation while valid
  neighbours, filters, totals, ordering, and pagination remain usable. (#786)
- Let Activity search find durable displayed values, including automatic,
  no-change, decision, and policy before/after wording. It streams immutable
  authority in order without an index, duplicate audit document, or new
  private output. (#787)

- Make Activity one current row per immutable change while retaining its
  ordered write lifecycle behind the row's bounded details disclosure. Search,
  filters, date bounds, totals, and pagination now operate on those change rows
  without a mutable audit projection or new persistence schema. (#788)

- Recover explicit EPUB package, front, and rear series memberships with their
  stated sequence while keeping advertisements, reading lists, prose, and
  trailing ambiguity out of local evidence. (#375)

- Keep one malformed Audiobookshelf title from pausing a healthy connection.
  Its trimmed public projection remains visible as needs-attention while valid
  sibling work continues without target mutation. (#797)

- Keep shared connection failures prominent only while owner action is needed.
  A successful check now gives one dismissible acknowledgement before yielding
  to the normal Library and Activity state; retained recovered records no
  longer restore the banner after navigation or restart. (#638)

- Keep Library as the per-work decision workspace, with provenance and bounded
  Evidence inline. Evidence and History remain read-only direct diagnostic pages
  with Back to work and no decision controls; no legacy redirect is provided.
  Long descriptions retain readable preview and full inspection. (#640)

- Present the strongest qualified publication fact once in Library, while
  keeping a month-level fact automatic only when its existing independent,
  manifestation-bound evidence remains intact. Automatic, recorded, actionable,
  and unavailable fields now retain their truthful owner-facing context. (#598, #810)

- Preserve an empty subtitle when eligible provider evidence contains only the
  exact generic marketing label `A Novel`. Legitimate and local subtitles stay
  reviewable; the v10 proposal policy requires a clean rebuild of v9 state.
  (#807)

- Record a qualified automatic series membership only when two independent
  provider lineages agree. Duplicate lineage, provider failure, and incomplete
  membership remain visible as a stable owner-review reason. (#809)

- Keep an empty ISBN field truthful when retained identifier lineage cannot
  produce an eligible alternative. Invalid values now retain their specific
  rejection reason rather than appearing as missing evidence, while unresolved
  applicability still defers. This advances the persisted proposal policy to
  v9; existing v8 review state requires a clean rebuild. (#595)

- Treat an automatic decision that preserves the current value as a durable
  no-change outcome. Canonically equal title, identifier, creator, and nullable
  values now bypass write authority, consent, backup, and target mutation, stay
  out of Attention, and remain visible in Activity as `No change required`
  after restart. (#599)

- Keep the medium Library Attention drawer clear of a wrapped masthead. Its
  fixed position now follows the shell's measured height with deliberate
  clearance, while preserving the drawer's existing scroll and focus behavior.
  (#813)

- Keep provider-recovery cutovers from freezing preparation or crash-looping
  startup: recovery jobs now enroll their exact scope atomically, failed outbox
  rows remain retryable while later rows drain, and claimed successors return
  through bounded typed preparation recovery. (#798)

- Keep Library and Attention responsive at 2,000 works. Transactional relational
  field facts replace repeated canonical JSON walks while immutable review and
  shadow documents remain authoritative; startup fails closed on disagreement,
  new works validate and project atomically, and each visible case is checked
  once against its exact admitted proposal and report rather than repeatedly
  evaluated. An exact-predecessor plan preserves state. (#802)

- Preserve expensive pre-release review state when a schema change adds only
  reconstructible derived objects. An explicit offline command now verifies a
  private backup and adopts #800's two indexes from its exact predecessor;
  ordinary startup and every unrecognized schema still fail closed. (#805)

- Keep Library reads bounded as review history grows. The exact schema now
  indexes each work's latest preparation row and each case's shadow outcome
  history; a 2,000-work count no longer repeatedly scans either history table,
  while canonical documents and search results remain unchanged. (#800)

- Stop idle preparation from repeatedly integrity-scanning the complete
  application database, and stop the guarded-write scheduler from reparsing a
  completed page cycle until real work wakes it. The server validates its
  locked SQLite inode once, keeping owner reads responsive after preparation
  completes. (#799)

- Say what an Activity record actually changed. Each row reads work, field,
  exact change, and result in the owner's words, with the before/after, who
  chose it, and whether Audiobookshelf confirmed it behind one disclosure; a
  Policy record lists each setting it moved with readable From and To.
  Automatic choices now show both Previous and New from their immutable
  decision authority, while recovery keeps its compact detail. (#636)

- Stop making the owner wait for a poll, and make write permission actionable
  where they already are. Preparation and completed works reach the open tab as
  they happen, over one authenticated stream that says what changed and never
  what it changed to. Waiting is stated as the four next steps it actually is,
  and Library and Activity carry one offer naming every destination `Enable
  writes` covers and the reason for each it cannot. (#641, #639)
- Show every cover whole, and stop making a decision wait for the Library. Each
  cover is contained at its own aspect ratio in a quiet 5:8 frame, and below
  900px the current one joins the same scrollable row as its alternatives. A
  choice is acknowledged at once and opens the next field from exact-case state;
  a recorded field can be revisited and changed until its effect leaves the
  reversible states. (#644, #635)
- Make Library browsing reversible and every work reachable. Rows are whole
  targets, the bounded window walks both ways, search narrows as it is typed,
  and works without review cases open to their values and sources. Direct
  work-to-work selection remains reliable across pages. All lenses stay in one
  compact row without a redundant headline. Attention remains above the selected
  work; Back appears only when the Works rail is hidden. (#642)
- Stop Policy reporting an activation the owner never made, name a fresh
  container's seeded profile the active baseline, and return every surface to
  a clean draft on exact revert. (#643)
- Let the type gate catch a test that binds a double into a port the double
  does not satisfy. `arg-type` is enforced for `tests/` and `scripts/` rather
  than suppressed, re-arming the guards written against it — one passed
  silently whenever the list it protects was built with `append`. The 48
  modules with findings are named in `pyproject.toml` as the backlog; the
  other 90 and every new one are gated. (#693)
- Hold every reserved test port with an open socket until its server binds it.
  One reservation under `tests/support/` now defines that for all five sites
  instead of one, and any site that cannot hold the socket to the point of use
  says why. This closes a latent window; no sighting was ever traced to it.
  (#774)
- Judge `bootstrap.composition`'s mutation result instead of its score. The 590
  mutants no test reached are 14 functions, not a cloud: 303 are accepted in
  `docs/development/testing-strategy.md` beside what does prove them, and 287
  had no prover at all and are now tested. The generator also stopped recording
  an empty reaching-test map, which reads exactly like a module nothing covers.
  (#773)
- Report the fault an operator can act on: a provider-recovery database that
  cannot be created names the disk failure rather than the transaction its own
  rollback could not find, and a cover stage that fails on its store durably
  records `persistence` instead of the anonymous `failed` every unlisted code
  collapsed into. That widens the review-work schema, so an existing database
  is rejected and has to be recreated. (#715, #734)
- Read a Library row at the width its seam declares, so the three trailing
  columns the test double never supplied are exercised through the reader
  rather than padded by it. (#711)
- Tell the truth about a guarded write that was refused. A storage fault is no
  longer reported as an unacceptable decision, a withdrawn consent or a revoked
  authority, nor escapes the currentness seams untyped as a 500; startup
  recovery attempts every pending plan and reports every failure rather than
  stopping at the first; and a permanent refusal now answers 409 instead of
  inviting a retry with 503. (#677, #678, #679)
- Stop a package mirror from spending the protected browser job's whole budget
  before a test runs: `playwright install` no longer carries `--with-deps`,
  whose only contribution on a Chromium cache hit was `apt-get`, and the step
  is time-bounded. A runner image that stops supplying one of Chromium's shared
  libraries now fails in a named launch step instead of reading as a
  browser-tier regression. (#772)
- Stop the continuous-review scale qualification failing for its own reasons.
  Its restart killed the first server, leaving the SQLite recovery sidecars the
  supported operating contract rejects, so the restart exited 1 before serving;
  it now stops that server and reports a leftover sidecar as itself. The review
  port is held by an open socket until the spawn rather than returned as a hint
  anything could take. (#770)
- Enforce that every direct dependency carries an authority grant.
  `scripts/verify_dependency_authority.py` fails the gate when `pyproject.toml`
  declares a dependency `docs/development/dependencies.md` does not grant, when
  a grant outlives its dependency, and when a grant's own spelled count no
  longer matches what it covers. (#769)
- Record the twelve checkpoints delivered since 0.10.1 in the roadmap, silent
  from 0.10.2 onward. Each outcome is transcribed from that milestone's own
  description rather than written fresh. (#755)
- Test the hypothesis that this suite carries deletable tests, and record that
  it does not. A coverage run and a mutation run over the highest-churn modules
  found four deletable lines: 48% of tests add no unique coverage yet assert
  distinct behaviour, so the audit's product is 38 new cases closing assertion
  gaps the measurement found — the CLI argument contract, the owner login
  throttle window and token bounds — and tooling to repeat it. (#737)
- Rebase Renovate branches only when they conflict, so an open update is no
  longer rewritten by unrelated activity on the base branch. (#768)
- Stop a route's deferred focus move from taking focus a reader already placed,
  which silently lost the key that followed. The protected browser tier's
  failure records now reach its log instead of pytest's discarded capture,
  naming the stage, URL, live regions and focused element, and a decision
  activation that reached no handler fails on that evidence instead of
  spending the locator timeout on an outcome nothing can produce. (#760)
- Enforce the documentation corpus's own claims on every pull request:
  `scripts/verify_documentation_integrity.py` fails the gate when a live
  document is unreachable from `docs/README.md`, a relative link does not
  resolve, or an ADR is missing its index row, shares a number, or is indexed
  but absent. The changelog guard now also requires each entry to name its
  issue, grandfathering by name the 39 written before that convention. (#686)
- Harden the container runtime contract: the image declares a `HEALTHCHECK`
  reading `/health/ready` over loopback HTTPS against the configured
  certificate as its own trust anchor, compose caps memory and processes at
  measured figures recorded beside them, and the default publication becomes
  `127.0.0.1:8443` with LAN exposure now a documented owner choice. (#738)
- Leave the documentation corpus smaller and truer, with one authority per rule.
  Prose describing a browser digest comparison #757 removed, and two
  justifications their code had outgrown, now state what the code does; the
  dependency, quality and product-boundary corpora each collapse to one
  authority; and the drifted composition graph, closed checkpoint ledgers and
  security output ledger become pointers. (#683, #684, #685)
- Make a failing gate identify itself. The browser tier no longer loses the
  keep-alive race that produced `Route.fetch: socket hang up`, a truncated
  traceback is retained around its exception line so a transport error's
  identity survives, the installed review server's start wait tells a crash
  from a timeout and reports the child's exit code, and the browser tier
  emits the payload record naming the Chromium it ran. (#730)
- Compress every changelog entry to the outcome it reports, in at most six
  lines with its issue reference, and hold future entries there with a guard
  test that names any entry over budget. No entry was deleted and no issue
  reference was dropped; the arithmetic, proof narrative, and review history
  each entry carried now live only in the pull request and issue it points to,
  which AGENTS.md and the documentation standards now say outright. (#717)
- Record the qualification browser payload instead of pinning its digest, so an
  auto-updating Playwright no longer blocks every bump on two hand-regenerated
  platform hashes. The digest, Chromium revision and Playwright version are
  emitted as evidence, the four failure causes report distinct reason codes, and
  a malformed, absent or symlinked payload is still refused. (#756)
- Report the failing tests when an installed suite fails qualification, with
  every secret-named environment value redacted first, and reconcile the
  documented check set with the only workflow: the dead contract tier and the
  uninvoked linked-issue validator are gone, `run_test_tier.py --list` runs,
  and CONTRIBUTING lists each check as gate-enforced or maintainer-manual.
  (#654)
- Format the backend with `ruff format` and gate it on every change. (#670)
- Make Library, Attention, and review detail use one truthful field
  classification: automatic abstentions return to owner review, automatic
  outcomes remain inspectable and overrideable, and unavailable fields explain
  their state without a fake empty-value choice. (#598)
- Convert every `tests/unit/` module to pytest, completing the suite
  conversion and retiring the last shared `TestCase` base. (#697)
- Convert every `TestCase` module outside `tests/unit/` to pytest, the pilot
  stage of the suite conversion. (#697)
- Make the application layer's error identities name the failure they report.
  The report parser refuses an unpaired surrogate as its declared type, so two
  reverted narrowings are restored; cleanup can no longer displace the failure
  it reports; policy staleness is read from a typed condition, not a word in a
  message; and the publication, reviewer, persistence and connection identities
  each name what failed rather than the outcome or the nearest seam. (#698)
- Put the last frontend test that waited out a dwell on a real 360 ms sleep
  onto the fake clock, and pin the 320 ms confirmation dwell to the status only
  its continuation writes, so a dwell that suspends nothing fails. Records why
  Testing Library's `userEvent` and `findBy*` cannot be used there -- the same
  reason `test.globals: true` is declined rather than adopted. (#699)
- Sweep the application layer's 94 broad exception handlers so none can turn a
  fault into a benign domain outcome: two narrow to the type their seam
  declares, the rest record the invariant that makes breadth correct. Five
  fixes carry tests -- a verifier fault read as a wrong password, a broken
  factory blamed on the stored credential, a read-back as a failed activation,
  later shutdown failures dropped, a lane ended by an unconverted read. (#698)
- State that a single-caller extraction is not judged on line count, and give
  the documentation an acceptance demands a fifth reporting bucket. The five
  reconcile to the net delta except where a formatting reflow forced by the
  change spreads implementation, left open as #719. #662's worked example now
  measures its reflow into the cover consolidation, landing it on 20 against
  20 — a tie the clause does not settle, left to #721. (#709)
- Widen the provider-recovery transaction epilogue to the ten sites #662 left
  longhand, with each adopting site naming the extra exception types it admits,
  so a widening is visible in the call rather than implied by which copy was
  pasted. No site's caught set changed, and no SQL, transaction nesting, or
  caller-visible error identity changed. `enqueue_jobs` and `resume_fanout`
  keep their longhand, because adopting either would add or drop a type. (#706)
- Take every path-based security decision in the web layer on the routed path
  rather than the URL rebuilt from the `Host` header, which a header or an
  encoded `%23` can split from the path the router dispatched on
  (GHSA-86qp-5c8j-p5mr). `Host: review.test/health/live#` made the trust
  boundary read a mutating API request as a health probe and skip both origin
  validation and the build-identity check. (#702)
- Pin Starlette 1.6.0 and FastAPI 0.141.1, clearing the six advisories that had
  left `dependency-audit` failing on `main` since `ecd70fcb`. Four reach no
  surface this service has; the two that did are the `Host`-derived ones fixed
  above, independently of version. Adds one transitive package,
  `annotated-doc` (MIT). (#702)
- Pin `httpx2` in the `dev` group so `starlette.testclient` stops emitting the
  deprecation line that fails `qualify_exact_artifact.py`'s installed-workspace
  probe, which treats any output as failure. Development scope only: absent
  from the shipped wheel's metadata and the container image, and it does not
  displace the runtime `httpx==0.28.1` the adapters call. (#702)
- Report the failure that actually ended a provider-recovery transaction. Two
  transactional write paths abandoned theirs with an unguarded `ROLLBACK`, and
  SQLite can end a transaction itself on `SQLITE_FULL`, `SQLITE_IOERR` or
  `SQLITE_NOMEM`, so on a full disk an operator was told the transaction was
  missing rather than that the disk was full. Both now carry the `in_transaction`
  guard; `open()`'s identical rollback is #715's, not fixed here. (#713)
- State which lines the code-economy consolidation test measures, so two
  readers measuring the same change reach the same verdict: all the
  implementation a consolidation adds, shared and forced on the former call
  sites alike, against all the implementation it deletes, leaving structure and
  demanded documentation in the reporting buckets. The test's severity is
  unchanged; two verdicts the ambiguity left open now settle. (#708)
- Make the Library work card's attention line agree with itself. The noun
  pluralised and the verb did not, so a work with one open field read "1 field
  need attention". It now reads "1 field needs attention", and two or more
  still read "2 fields need attention". No assertion covered the string, in
  any tier, which is why three green tiers passed over it; one now pins both
  forms. (#705)
- Make the review-web test double produce the Attention row the repository
  actually produces, and delete the two `len(row)` guards it was propping up:
  those guards protected the fixtures, not the product. The double now emits
  the automatic outcome and cause keyed by destination, and a new test pins the
  three classifications an owner is shown for suspended, abstained and ordinary
  fields. No product behaviour, SQL, or projection changed. (#703)
- Give the duplicated provider and review-surface logic one home each: a shared
  provider-recovery commit/rollback epilogue, one `ProviderFailure` identity
  across the three metadata adapters, sentence-case labels so `media_kind` no
  longer reaches the owner three different ways, and one hook behind the two
  cover layouts. Two further consolidations were declined on measurement and
  the longhand kept. (#662)
- Keep installed pytest plugins and oversized parameter names out of the test
  tiers. A tier now passes `--disable-plugin-autoload`, so an installed
  `pytest-randomly` or `pytest-xdist` can no longer reorder, shard or drop
  cases with nothing rejected, and cohort membership is unchanged. A long
  parameter set no longer pushes a case identity past the stall diagnostic's
  512-character bound. (#661)
- Stop the test-tier runner honouring ambient pytest configuration, and give it
  unittest's accounting. `PYTEST_ADDOPTS='-k passing'` could reduce a cohort to
  its passing subset and report the tier green with a failing test unrun; a tier
  now refuses any pytest variable reaching collection, execution, plugin loading
  or the diagnostic streams. Class-scoped setup failures count once, an
  unexpected success fails the tier, and native identities are path-free. (#661)
- Split the frontend's 2,710-line router test into five route modules, and
  record the suite's standing test conventions as a `Test economy` section in
  `docs/development/testing-strategy.md`. (#661)
- Split the four oversized backend test files into per-concern modules, so a
  failure names what broke instead of reddening a 5,000-line file. (#661)
- Run the public test tiers on pytest without changing what a tier is, opening
  fixtures, `parametrize`, and assertion introspection to every future test in
  the suite. Every cohort manifest is byte-identical to the unittest engine's.
  (#661)
- Distinguish consolidation from relocation in the code-economy rules, so the
  requirement that an abstraction be lighter than what it replaces stops being
  read as a bar on moving code to where it belongs. A single-caller helper may
  now earn its place by carrying a name, a testable contract, or an enforceable
  boundary. (#692)
- Make four stale type declarations state what the code actually produces and
  consumes, and delete the casts that carried the difference. No wiring,
  message, or runtime output changed. (#668)
- Declare the automatic-safety seam the served composition already binds, so a
  store missing the safety members is caught by a signature instead of
  surfacing at runtime as "automatic safety is unavailable" on every safety
  read, pause, suspend and recover. (#667)
- Compose the `policy-preview` CLI only from a contract its repository
  satisfies in full, deleting six `# type: ignore[arg-type]` suppressions and
  the latent `AttributeError` behind them. Output, exit codes, and failure
  messages are unchanged. (#667)
- Move review-pipeline construction, provider-recovery activation, and policy
  resolution out of the 2,418-line composition root into four owned application
  modules, which gain their destination layer's import constraints. Nothing
  about wiring changed. (#660)
- Decompose the 1,131-line `_package_report` EPUB extractor into twenty-nine
  named stages, so what the extractor accepts, rejects and bounds reads in one
  place instead of being inferred from a 285-branch function. Extraction
  behaviour is unchanged. (#659)
- Split the 9,943-line SQLite metadata-report repository into nineteen focused
  modules, with no import, application port, or call site rewritten. One
  behaviour changed: 56 hand-rolled transaction sites now share one re-entrant
  scope, so a nested repository call undoes only its own writes instead of
  silently committing the caller's partial work. (#658)
- Express field validation once, as a shared vocabulary applied declaratively,
  rather than expanding the same checks longhand for every field. In the
  browser a malformed case-detail field is now rejected as an invalid response
  instead of raising `TypeError`. (#663)
- Report a malformed bibliographic combination as a packet error rather than a
  `TypeError`. Nothing changes about what is accepted. (#669)
- Fail closed on two malformed inputs the boundaries that own them were meant
  to reject: a shadow-review correlation whose identity lists are not strings,
  and `provider-recovery` composing a pipeline over an absent review
  configuration. Both now raise the error their sibling paths already raise.
  (#669)
- Report a failed field-effect claim as what it was, so `claim_conflict` again
  means an actual concurrency conflict. A paused control table, suspended
  capability, revoked authority or consent, and storage fault each surface
  under their own code, and every one but the paused table now returns 503
  rather than 409. (#656)
- Report known-vulnerable Python dependencies in CI, so the backend half of the
  supply chain is audited on every change. A `dependency-audit` job scans the
  complete `uv.lock` closure with a digest-pinned OSV-Scanner; the only
  mechanism that may hold a failure open is the time-boxed, gate-checked
  register `osv-scanner.toml`, which is empty. (#653)
- Verify merged `main` and the shipped container in the public gate: two
  individually green pull requests that break `main` together are now caught, a
  Dockerfile regression fails the gate rather than a maintainer's manual
  ritual, and the tier runner emits each failing test's bounded traceback.
  (#655)
- Drive the Activity search debounce deterministically in the router test that
  failed in roughly a quarter of full-suite runs. No product behaviour changed.
  (#661)
- Gate the Python backend on ruff and strict mypy in the public gate, so
  `py.typed` and the `Typing :: Typed` classifier describe a verified property.
  Fixing the findings corrected one real defect: an invalid shadow-review
  packet raised `NameError` out of the handler meant to report it. (#652)
- Make a clean frontend checkout pass every documented source check without a
  manual ritual: the gitignored DTOs regenerate first, scratch space is
  excluded from lint, and `frontend/src` is Prettier-formatted under committed
  configuration. (#657)
- Keep retained metadata provenance at each Library review choice: current
  local presence, named corroborating sources, rationale, deterministic
  recommendation status, and limitations can be inspected in place without
  replacing the owner decision workspace. (#640)
- Pause only review-preparation work that shares an unavailable Audiobookshelf
  connection. The owner can run one authenticated, CSRF-protected bounded
  connection check; recovery resumes the retained checkpoint without rerunning
  completed packages or provider work. (#638)
- Run one configured, qualified cover-reviewer comparison during ordinary
  review preparation before the review package is published. Advice remains
  immutable, exact-current, and owner-only; unavailable or failed reviewers
  retain the manual comparison and never gain cover-write authority. (#600)
- Preserve valid unlabelled Hardcover work creators as neutral, identity-bound
  evidence, and admit one deterministic authors proposal only when a distinct
  provider's complete explicit author set exactly corroborates the same
  qualified work. Partial, reordered, mixed-role, malformed, and same-authority
  sets remain unresolved. (#416)
- Add durable provider- and configuration-scoped recovery canaries. Terminal
  packages register only retry-eligible lanes, shared failures publish nothing
  and retain bounded backoff, and stale work and disabled configuration stop
  safely without target metadata writes. (#408)
- Add the internal, unreachable scoped provider retry-successor seam. It
  rebuilds only explicit retry-eligible failed lanes from an exact-current
  report and adds no scheduler, endpoint, canary, or write path. (#629)
- Complete first-generation staged review projection before its package becomes
  owner-visible, while retaining published-package authority for Library,
  Attention, owner decisions, and field effects. (#630)
- Record terminal provider failure recovery eligibility as durable,
  configuration-generation-scoped waiting or dormant state. Operators increment
  `provider_recovery_generation` and restart after fixing a dormant provider
  condition. (#625)
- Make Library policy impact previews distinguish real changes from retained
  values, unsupported evidence, and owner review. An empty field with no
  candidate now reads `No change · remains empty — no supported value found`,
  and exact-current, ignored, and abstained outcomes no longer inflate the
  change total. (#609)
- Explain every closed Library policy representation in place: its admitted
  source and value, bounded transformation, field effect, and abstention
  boundary. Visible Google text now never falls back to Hardcover, and any
  Automatic-to-Ask-me consequence is owner-visible. (#608)
- Record an equal automatic metadata candidate as a durable **No change
  required** Activity outcome without creating a guarded write plan or
  consulting write authority, consent, capability, backup, target, or read-back
  services. (#599)
- Show each Library policy chapter's exact changed-field count, including zero,
  with chapter-specific accessible labels. Unsupported fields now explain that
  they are read-only and cannot be changed in a draft. (#607)
- Keep authenticated Library and Attention reads bound to one completed review
  package. In-progress, stale, and failed preparation no longer replaces a
  visible prior package or exposes a partial result, and pre-package failures
  appear as bounded Library-only recovery states that never enter Attention.
  (#597)
- Preserve safely anchored Hardcover work identity, title, and complete creator
  evidence when the requested edition enrichment exceeds its bound. The
  oversized enrichment is diagnosed without retaining any edition claims or
  cover evidence. (#418)
- Fix Audiobookshelf nested ebook identity handling so catalogue discovery and
  current-item validation derive the same canonical service-relative location
  and opaque file identity. Conflicting, traversal, missing, and ambiguous file
  records remain fail-closed before currentness can admit a write. (#596)
- Compare checksum-valid ISBN-10 and ISBN-13 forms from one canonical family as
  one proposal, retaining each original evidence reference while preferring an
  already observed ISBN-13 representative. Invalid identifiers and distinct
  ISBN families remain separate. (#595)
- Fix Linux SQLite connection validation to prove the exact opened `main`
  database path against the configured device and inode. Authenticated
  review-preparation status polling now stays available and snapshot-consistent
  while concurrent writers churn rollback-journal descriptors. (#594)
- Add the protected owner-only Activity ledger: a server-paged, date-bounded
  record of durable review decisions, policy transitions, safeguards, guarded
  writes, and verification, with search, filters, and bounded evidence
  inspection. No queue, retry system, export, or write authority was added.
  (#591)
- Implement the protected Library policy notebook: one active policy and one
  mutable draft present as four focused chapters, and a single whole-draft
  action validates, snapshots, and previews impact before reversible guarded
  activation bound to the policy identity used for that preview. (#590)
- Implement the protected Library review workflow: wide entry selects the first
  ordered reviewable work, current-versus-exact-value choices are one command,
  and a truthful review-complete boundary precedes deliberate next-work
  continuation. Recorded decisions remain separate from apply and read-back
  verification. (#589)
- Activate owner-confirmed semantic cover decisions in the installed review
  product. One exact-current cover field shows the incumbent and up to 12 safe
  report-bound alternatives, runs the contained observer and deterministic
  policy as advisory evidence when configured, and lets the owner keep, select,
  or take no action. (#501)
- Reconcile the 0.15.0 source-only semantic-cover workbench into one
  aggregate-safe maintainer review package proving useful reusable benchmark
  behavior. This is not an unseen-generalisation or runtime-activation claim,
  and #501 remains unpromoted. (#544, #500)
- Make source-only normalized cover transports byte-identical across supported
  platforms: a small fixed PNG serializer replaces an accidental dependence on
  the local Pillow/libpng/zlib writer. Pixel equality, image limits, and
  reviewer authority boundaries are unchanged. (#542)
- Bind the public F20 candidate identity and terminal rich-field qualification
  statuses into policy validation, API, CLI preview, and the owner notebook.
  Qualification admits the series, exact ISBN, genre and tag list, and safe
  plaintext description options to decision-only Automatic; tags stay off by
  default and every other rich option stays manual. (#564)
- Record the F20 rich-metadata qualification candidate from 49 accepted
  real-work reports and 421 retained-response replays. Subtitle does not pass,
  `explicit` remains human-only, and narrators and cover remain excluded.
  (#565)
- Add the closed v2 rich-metadata policy vocabulary for manual preview:
  subtitle, series, exact list operations, bounded description text, and exact
  ISBN/ASIN choices. Rich-field automatic intent remains unavailable and no
  target or media write authority is added. (#566)
- Fix source-only contained cover-reviewer image transport so the Linux sandbox
  receives each rehashed, sealed preview through a descriptor-data mount
  instead of a `/proc` path mount, and oversized descriptor copies fail before
  dispatch. (#542)
- Add a source-only deterministic cover-policy report over exact packet,
  identity and closed observation evidence. Three typed booleans can avoid
  promotional text or tie-in branding and prefer minimal composition;
  unavailable or unbound evidence abstains or fails closed. (#543)
- Recognize the one proven transient local contained-reviewer CLI abort without
  widening reviewer authority: only a bounded strict pre-terminal JSONL abort
  receives one fixed one-second retry in a fresh job and child process. Every
  other failure remains terminal. (#543)
- Reduce private Actions consumption without recombining the qualification
  suites that previously timed out: run the three exact-head pull-request jobs
  only for candidate commits, and retire the remote linked-issue runner in
  favour of the trusted controller's exact closing-reference proof. (#571)
- Add a source-only cover-observation report with one deliberately small,
  policy-neutral visual vocabulary. Each closed observation binds its exact
  packet, candidate, preview, taxonomy, schema, and observer identity;
  invented, duplicate, hostile, or unbound results fail closed. (#541)
- Preserve bounded rich metadata evidence for manual review without widening
  automatic authority. Advance the exact-current shadow-review packet to v6 so
  typed taxonomy citations cannot be confused with the prior packet shape.
  (#563)
- Add a source-only, report-local cover identity projection that classifies
  each retained cover as verified, plausible, unknown, or conflicting from
  bounded retained evidence. Plausible never means verified, and the projection
  makes no provider call and holds no persistence or write authority. (#540)
- Polish the authenticated interface without changing its authority: the
  asymmetric book-spine shell mark and touch-safe secondary actions return,
  Policy becomes a one-chapter-at-a-time notebook with an explicit validate,
  preview, impact, and activation cadence, and root entry routes to sign-in.
  (#555)
- Restore the Variant M Library review workspace: one permanent Library
  destination with a paginated, recoverable URL-owned Attention queue, direct
  current-or-specific value decisions, bounded readable evidence, and a
  responsive flow that stages the same information on phone layouts without
  dropping it. (#554)
- Admit deterministic report-bound cover packets to the source-only contained
  reviewer with opaque digest and order identity, incumbent-preserving
  deduplication, fixed preview and byte limits, and no runtime,
  production-library, or write authority. (#542)
- Normalize bounded report-bound RGBA cover pixels into deterministic,
  metadata-free PNG transports before isolated review. Malformed, foreign,
  changed, oversized, or non-canonical inputs still fail closed. (#542)
- Reconcile and qualify the complete 0.14.1 authenticated interface against its
  exact surface-specific references. The packaged source, wheel, and container
  retain one persistent responsive shell without expanding metadata or write
  authority. (#534, #499)
- Restore Activity as the calm, readable guarded-work ledger: explicit consent
  and readiness with backup explanation, continued history during partial
  consent, scoped pause and revoke controls, and exact grouped recovery without
  queue, retry, or invented progress authority. (#533)
- Restore Policy as the refined semantic notebook with Identity, Publication,
  Curation, and Unsupported content chapters, readable active-versus-draft
  state, and an explicit activation review over the existing guarded
  currentness-bound operation. (#536)
- Restore Variant M review detail: direct current-or-specific-proposed value
  controls record the existing exact-field decision without a submit step. When
  a decision response cannot be reconciled against authoritative detail, every
  field stays blocked until a later successful read restores eligible choices.
  (#531)
- Restore the Variant M editorial shell and complete attention-first Library.
  Rows lead with readable work identity and truthful field counts, Attention
  groups only human-beneficial fields with bounded reason and exact-field
  links, and discovery includes current work before or without review
  admission. (#535)
- Reconcile the exact 0.14.0 application contract and finite deflation
  inventory, remove duplicated retired-asset assertions, and record that the
  separate 0.14.1 interface-fidelity milestone remains the smallest follow-on
  before a first-release decision. (#497, #498)
- Activate Library, Attention, review detail, Policy, and Activity in one
  persistent authenticated React shell at their existing URLs, with CSRF and
  build-identity mutation gates and authoritative recovery, without expanding
  product authority. (#496)
- Establish an exactly pinned React, strict-TypeScript, Vite, TanStack Router
  and Query browser platform without switching current routes. Exact OpenAPI
  types, hashed manifest-bound assets, and wheel and container exclusion of
  Node prepare the separate authenticated cutover. Series values now retain
  their sequence in review display. (#495)
- Compose authenticated browser workflows behind one application boundary.
  FastAPI now receives named operations instead of a broad repository, and
  unconfigured scan, retry, pause, and resume controls preserve the typed
  `unavailable` response instead of reporting a persistence failure. (#494)
- Remove the retired qualification-only shadow-review product workflow, its
  persistence, browser model-advice projection, and qualification machinery.
  The deterministic packet and automatic-decision contracts are preserved, and
  one source-only contained-reviewer workbench kernel is retained. (#493)
- Contract the installed command surface to the authenticated `review-serve`
  launcher. Catalogue, report, batch, proposal, case, decision, and
  contained-reviewer experiments move to a typed source-only contributor
  workbench excluded from the wheel and container. (#492)
- Label persisted qualified-automatic Activity events accurately, leave
  unrecognised origins neutral, and keep eligible prepared libraries available
  for authenticated, CSRF-bound later consent after another library is already
  enabled. Library revocation remains scoped to that library. (#507, #508)
- Let Activity consent proceed for the currently eligible fields when another
  field is unavailable or lacks permission, while connection outages still
  block consent. Capability-disabled fields no longer inflate Library or
  Attention counts. (#505, #506)
- Persist denied field readiness across restart, preserve unexpected-behaviour
  suspensions during read-only probes, disclose and bind the exact destination
  subset for initial and incremental Activity consent, and restore clear
  qualified-automatic versus neutral event-origin labels. (#505, #506, #507,
  #508, #510)
- Keep a returned automatic field on its durable manual hold until the
  requested exact-current scan completes successfully, and bind release to the
  originating human decision so a concurrent newer hold cannot be cleared. The
  accepted request survives restart and is recovered through bounded rotating
  pages without starving later requests. (#503, #504)
- Bind the exact-container checkpoint probe to current persisted synthetic
  consent, and correct the deployment contract for the composed guarded
  Audiobookshelf target and its write gates. (#509, #510)
- Add continuous guarded application for explicitly consented Audiobookshelf
  libraries. Human and sealed qualified automatic decisions share one
  14-destination kernel with exact-field value guards, pause and revocation,
  durable manual holds, and restart recovery. Audiobookshelf supplies no CAS,
  so the documented same-field lost-update window remains. (#153)
- Add the supported Audiobookshelf guarded-write canary: one
  destination-minimal metadata or digest-verified cover request follows a final
  authoritative pre-read and is followed by read-back reconciliation.
  Authentication failures suspend the connection; permission and field faults
  suspend only the affected destination. (#150)
- Add the supported single-container operational substrate: absent or
  exact-current startup, readiness and liveness, graceful claim draining, typed
  scoped incidents, and previewable re-redacted support bundles. The container
  remains non-root with one volume, one HTTPS port, and read-only media.
  (#151)
- Add the origin-neutral guarded field-effect kernel for immutable owner and
  sealed qualified automatic decisions. All 14 ebook destinations use one
  deterministic field plan, a repository-atomic authority, consent and pause
  claim, and target-side optimistic concurrency. The only writable target is a
  deterministic fake. (#149)
- Qualified metadata-policy defaults now create immutable, decision-only
  automatic dispositions for the sealed F19a envelope. Preview, owner pause,
  durable safety suspension, and explicit recovery keep unresolved or suspended
  fields in human Attention, and no write capability is added.
- Add an explicit metadata-policy Preview posture that records immutable,
  non-authoritative F19 outcomes as newly eligible exact-current cases pass the
  existing background projection stage. Replay is idempotent, stale work
  publishes nothing, and Library and Attention work is unchanged. (#478)
- Activate an exact reviewed metadata-policy draft with one atomic generation
  cutover. Old-policy cases retire before successors progressively return
  through the existing review pipeline; Ask remains manual and unqualified
  Automatic remains unavailable. A newly configured library stays
  proposal-ineligible until the next explicit activation. (#477, #343)
- Add closed, canonical metadata-policy draft and sparse per-library preview
  contracts. The built-in policy stays active; preview creates no activation,
  queue authority, decision, apply, or target/media write capability.
- Activate bounded continuous review reconciliation in the supported container:
  first-start and interval scans coalesce through the persisted two-lane
  pipeline, fair claims and provider circuit state survive restart, and the
  protected M shell gains one compact activity popover with pause, resume, and
  eligible retry. The path remains decision-only and write-free. (#475, #473)
- Tier public qualification into independent required fast-source,
  exact-installed, and protected-browser jobs while preserving one stable
  aggregate gate. Each tier has a documented command and path-free cohort
  manifest, and bounded evidence distinguishes its failure kinds. (#482)
- Add owner-requested progressive library preparation to the authenticated
  review surface. Catalogue, report, ProposalSet, and ReviewCase work is
  persisted, duplicate-safe, restart-safe, and advanced across two fixed lanes.
  It stays decision-only and write-free. (#474)
- Restore the authenticated review frontend to the protected variant M visual
  contract across Library, Attention, and exact-current Detail, qualified at
  1440, 884, 390, and 320 CSS-pixel widths. Theme selection is a
  presentation-only secure cookie and adds no new authority. (#455)
- Reconcile immutable decision retries with authoritative state. A committed
  same-key, same-payload request now replays its original acknowledgement even
  after case supersession, while changed payloads retain an explicit conflict,
  and the browser never claims an ambiguous decision was unrecorded. (#456)
- Preserve the selected Library lens when searching and keep Attention search
  on its own route. Title, library, and item matching applies one
  locale-independent Unicode casefold contract on both sides of the bounded
  SQLite query, and cursor encoding rejects an out-of-range next offset. (#461)
- Make every evidence reference in an authenticated exact-current case
  reachable through deterministic, case-bound pages of at most 50 items, loaded
  progressively without provider access or new decision authority. (#458)
- Read each authenticated exact-current case, Library page, and Attention page
  from one authoritative SQLite snapshot, so concurrent supersession or
  decision recording can no longer mix stale currentness, evidence, history,
  rows, or counts within one response. (#457)
- Add one supported canonical-HTTPS path for `review-serve`. Certificate and
  key configuration is validated as a matching, owner-controlled,
  descriptor-bound identity with an owner-only key before database or listener
  startup, and the server has no plaintext escape hatch. (#459)
- Align the authenticated review API contract with runtime behavior. Protected
  operations declare the owner API-key cookie and required mutation headers,
  and logout distinguishes invalid sessions, CSRF mismatch, and revocation
  outages without clearing a still-active cookie. (#460)
- Project exact-current model advice from only the latest persisted
  reevaluation attempt. A newer pending attempt is no longer hidden by an older
  failure, and a newer failure cannot resurface stale successful advice. (#462)
- Activate the complete F16a browser round trip. The authenticated owner can
  choose `accept`, `keep_current`, or `reject` directly from an exact
  unresolved field, receives only a server-acknowledged success message, and
  advances to the next unresolved field. Automatic-only cases remain read-only.
  (#450, #152)
- Add the third F16a production increment: an authenticated client can record
  one immutable `accept`, `keep_current`, or `reject` decision for one
  exact-current ordinary field. SQLite publishes the decision and its audit
  event atomically and rejects stale, conflicting, or already-decided attempts.
  (#449)
- Add the second F16a production increment: authenticated bounded library
  lenses and a separate exact-field attention queue. The default view places
  genuine unresolved human-attention work first while automatic-only cases
  remain searchable and inspectable without controls. (#448)
- Add the first F16a production increment: an authenticated read-only
  `review-serve` boundary for one exact-current ReviewCase, bounded evidence,
  admission history, and deterministic authenticated OpenAPI, with opaque
  SQLite-backed sessions and Argon2id verification. (#447)
- Add `shadow-decision-preview` for one exact current ReviewCase. It opens an
  existing report database read-only, compiles the exact admissible action
  catalogue, emits one canonical deterministic decision per proposal, and
  safely selects manual on ambiguity, conflict, or incomplete closure. (#384)
- Accept bounded duplicate-free reviewer JSON with insignificant formatting
  variance at the transient provider boundary, retaining exact raw bytes and
  canonicalizing before typed domain construction. Incompatible nonmanual
  actions can no longer satisfy the same schema branch. (#440)
- Make the qualification shadow-review structured-output schema acceptable to
  the provider by expressing the same exact compiled action variants with the
  supported nested `anyOf` union. Domain validation still requires one exact
  compiled action and its complete citation closure. (#438)
- Bind the final qualification shadow-recommendation contract. Manual advice
  may retain an empty required `problems` list when it has a bounded reason and
  exact citations but no adverse fact, and insufficient, conflicting, or
  out-of-catalogue support goes to manual review without output repair.
  (#419, #420, #421)
- Compile a deterministic, packet-only catalogue of policy-admissible
  `recommend_candidate`, `recommend_keep_current`, and `manual` shadow-review
  actions before provider dispatch. Each action has a stable identity and an
  exact citation closure; rejected nonmanual actions retain typed exclusions
  and unknown obligations fail closed. (#431)
- Isolate ordinary qualification-reviewer provider state from primary
  credential material: status and inference mount a disposable per-attempt
  `CODEX_HOME` and nest only descriptor-bound `auth.json` read-only. Requested
  reviewer identity is persisted separately from provider-observed model,
  route, tier, effort, usage, and cost. (#433)
- Add bounded deterministic decision summaries to every shadow-review proposal
  and candidate while retaining the complete raw citation projection. Proposal
  summaries state only whether the closed policy found sufficient evidence,
  evidence-backed preservation, or a manual outcome, and grant no authority.
  (#423)
- Bind dependent title, subtitle, and series advice to explicit
  `bibliographic-relationships-v1` combinations without rewriting source
  strings. Every combination selects an already-admitted candidate or preserves
  the exact target-field state; when no safe tuple exists the affected results
  stay unresolved. (#425)
- Evaluate language and publisher candidates through a closed field policy
  without collapsing their original values or provenance. Canonical ISO 639
  aliases and non-contradictory script or region detail are acceptable, and the
  new non-selecting `acceptable_choice` outcome retains every acceptable raw
  alternative. (#424)
- Project publication-date evidence to its strongest independently supported
  precision: agreeing days remain full-day candidates, differing days in one
  month produce one `YYYY-MM` candidate, different months retain only shared
  year support, and different years remain conflicting. The runtime stays
  report-only. (#422)
- Preserve publication-date precision and incompatibility explicitly. Date
  links distinguish exact, month-compatible, year-compatible, finer-conflict,
  event-incompatible, and manifestation-incompatible observations, and provider
  dates vote only through an exact checksum-valid ISBN-family binding. (#409)
- Preserve high-confidence work-scoped title and complete ordered creator
  agreement when original values differ only by one closed, explainable
  comparison form: exact bytes, NFC, case and whitespace normalization, mapped
  punctuation, or conservative single-author `Family, Given` inversion.
  Diacritic stripping and fuzzy values remain disagreement. (#399)
- Preserve every independently valid, bounded Google Books, Hardcover, and Open
  Library claim or closed rich-evidence value from a safely anchored provider
  record when an optional sibling is absent, malformed, oversized, or untyped.
  An over-cap collection is rejected as one field without retaining a prefix.
  (#400)
- Coalesce semantically identical provider discovery lookups within one
  report-only observation. Each discovery lane retains its own provenance while
  replaying the immutable completed provider result, so coalescing does not
  create a cross-report cache or widen provider request authority. (#398)
- Expose one closed, aggregate-safe provider and reviewer failure taxonomy
  without copying exception text, credentials, paths, prompts, packets,
  provider bodies, process streams, headers, timings, or digests into public or
  durable status. Hardcover HTTP 200 GraphQL errors are terminal, never
  success. (#381)
- Preserve provider publication evidence with the same closed event and
  precision vocabulary as local EPUB evidence. Google Books publication strings
  remain visible but non-selectable, and the first actual provider dispatch
  records one canonical UTC access time that retries and replay do not refresh.
  (#382)
- Admit source-explicit target-work subtitle, publisher, and imprint from the
  bounded rear EPUB indicia pass under the same rules as front matter.
  Copyright holders, printers, distributors, production, and other-work
  material remain excluded, and rear position supplies no candidate authority.
  (#377)
- Recover source-explicit contributor roles and selected-manifestation
  publication dates from bounded package, front, and rear EPUB indicia.
  Production, conversion, retailer, and workflow credits remain
  contextual-only, and unsigned date-times now fail closed rather than being
  guessed. (#374)
- Recover from narrowly classified transient metadata-provider failures within
  provider-local fixed retry, pacing, byte, deadline, and dispatch bounds.
  Google Books permits at most 54 attempts, Hardcover 27, and Open Library 49;
  rate limits and provider-declared GraphQL errors remain fail-closed. (#391)
- Restore current Hardcover ISBN and complete-title/author GraphQL operations
  within the provider's three-relationship-depth limit. HTTP-success responses
  declaring bounded GraphQL errors now yield `unavailable` with no candidates,
  and malformed contribution roles stay field-local typed claims. (#378)
- Add Google Books as an ordinary report-only metadata provider with
  credential-free and optional controller-owned API-key modes, fixed bounded
  ISBN and work discovery, evidence-only publication strings, typed transport
  and quota outcomes, and deterministic replay. (#315)
- Keep a controlled package-metadata `Unknown` creator visible in report
  evidence while preventing it from becoming an authors replacement by itself.
  Mixed real creators retain their order, and legitimate anonymous, nonexact,
  provider, and opening-matter names remain eligible. (#376)
- Accept order-insensitive, repeated known reviewer citations at the untrusted
  output boundary, then persist and replay one deterministic sorted unique set.
  Unknown, missing, malformed, mixed-validity, and over-bound citations remain
  invalid, and exact raw bytes stay private. (#379)
- Keep EPUB ISBN recognition occurrence-local and reject checksum-valid
  ISBN-shaped values explicitly owned by a non-ISBN package scheme or URI.
  (#372)
- Search every distinct checksum-valid local ISBN-10/13 family through its own
  bounded provider lane, alongside one complete-title/author work lane when
  available. Reports retain exact triggers, lane outcomes, convergence,
  conflicts, and typed overflow without ranking, selection, or write authority.
  (#373)
- Harden the isolated qualification reviewer schema: typed bound constraints,
  pinned subset validation and request/digest binding, a real one-case
  empty-job canary through the production execution gate, and durable
  malformed-document versus semantic-validation failures. (#380)
- Correct historical documentation attribution: the 0.4 checkpoint had one
  `metadata-report` command and a two-table report store, and the 0.9
  checkpoint remains evidence-and-ProposalSet-only; review admission and shadow
  advice belong to 0.10. (#370)
- Accept the pinned Codex CLI's exact canonical ChatGPT subscription status on
  either process stream only when the other stream is empty and the command
  succeeds. Mixed, additional, malformed, API-key, and nonzero results remain
  rejected. (#368)
- Isolate qualification reviewer authentication under the exact pinned
  Bubblewrap artifact with an immutable empty `config.toml` data bind. Auth now
  uses only supported Codex CLI command scopes; it adds no model invocation or
  normal activation authority. (#365)
- Add the qualification-only isolated Codex shadow-review path for one exact
  current ReviewCase. It freezes the executable, model, prompt, profile,
  packet, output, and policy tuple, and supports no-egress packet preview, one
  foreground request, and offline inspection. No decision, apply, write, or
  normal activation authority is added.
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
  revalidates one exact current `metadata-report-v8` target, media snapshot, ABS
  cover, and private artifact authority, then evaluates a fixed conservative
  policy into exactly one closed result for each of the 16 Audiobookshelf
  destinations without provider calls. Publication remains append-only and adds
  no review, decision, or write authority.
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
