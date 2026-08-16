# Changelog

## Unreleased

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
- Widen the provider-recovery transaction epilogue to the sites #662 left
  longhand, with each adopting site naming the extra exception types it admits.
  `_rollback_on_failure` now takes them as `*wrapped` and concatenates them onto
  the `sqlite3.Error, OverflowError` pair its adopters share, so the six narrow
  sites keep their set by passing nothing, and a widening is visible in
  the call rather than implied by which copy was pasted. Ten sites pass
  `ProviderRecoveryError`; `complete_success`, which unpacks a health row
  positionally, also passes `AttributeError`. No site's caught set changed.
  The group is ten, not the nine #706 counted: `complete_missing_jobs` spreads
  the same handler over four physical lines rather than one, so normalising
  whitespace merges it into the group instead of leaving it a variant. Nine of
  the ten sat directly beneath the `ProviderRecoveryStoreError` re-raise the
  helper already owned, and since the helper is a context manager wrapping the
  whole body, there is no way to adopt one handler of such a pair without the
  other — each adoption removes both, which is why ten sites delete twenty-one
  guarded rollbacks.
  `provider_recovery.py` goes 1,062 to 986, a net `-76`: implementation `-86`,
  twelve lines added — one shared `except` and eleven `with` lines, each
  replacing a `try:` — against ninety-eight deleted; structure `0`, because the
  signature is rewritten in place rather than added; and `+10` of the docstring
  #706's acceptance asked for. Twelve against ninety-eight passes the
  consolidation clause, and since every added line replaces a deleted one it
  passes just as clearly on net-new implementation of zero. The clause's
  structure exemption is not needed to reach that verdict.
  `tests/unit/test_provider_recovery.py` goes `+168 / -11`, a net `+157`, which
  the refactoring rule's four buckets reconcile as nothing moved in or out,
  `+10` of structure the change required, no copies removed, and `+147` of
  checks newly added. Test docstrings and comments are counted inside that
  `+147` rather than broken out, because each documents the check it sits in;
  the underlying three-way split is implementation `+98`, documentation `+49`
  and structure `+10`. The `+147` is `+135` for the five new tests and `+12`
  net for generalising the connection proxy from refusing every `COMMIT` to
  raising a supplied failure, which is what lets a test set a claim up through
  the proxy and only then arm the error the epilogue must meet. The `+10` of
  structure is one `import ast`, the new class's declaration and its five
  method signatures, and the rewrapped signatures of that proxy and its
  factory, which now take a `BaseException | None` where they took a `bool`.
  Counted by region instead of by bucket, the five tests are `+142` and the
  proxy work `+15`. No copies were removed because the change consolidates
  nothing here; the new class borrows the existing `_store_over` rather than
  copy it, so the duplication it would otherwise have added never arrives. A
  test module that grows by the checks it gains is the outcome that rule calls
  a success.
  Four of those tests pin the three distinct caught sets from both ends — what
  each set wraps and what it still lets escape untouched — and the fifth pins
  every one of the seventeen `_rollback_on_failure` calls against the function
  that makes it, so the six narrow ones cannot be re-scoped underneath the
  behavioural tests. It accumulates those calls rather than assigning them, so
  a function holding two states both; assigning let the later call overwrite
  the earlier, which pinned seventeen functions while claiming seventeen sites.
  #662's mutation check still bites: deleting the guarded rollback from the
  shared handler fails three of them.
  No SQL, transaction nesting, or caller-visible error identity changed.
  `enqueue_jobs` and `resume_fanout` keep their longhand, because adopting
  either would add or drop a type rather than name one. The helper's docstring
  names both and why, so the shared baseline is not mistaken for a module
  invariant and neither path is later widened into agreement with it. (#706)
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
  of the store's transactional write paths abandoned theirs with an unguarded
  `ROLLBACK`, and SQLite can end a transaction itself when a statement fails
  with `SQLITE_FULL`, `SQLITE_IOERR`, or `SQLITE_NOMEM`. The rollback then
  raised `cannot rollback - no transaction is active`, and that became the
  `__cause__` of the reported `ProviderRecoveryStoreError` while the failure
  worth reading survived only as `__context__`. On a full disk an operator was
  told the transaction was missing rather than that the disk was full. Both
  handlers now carry the `in_transaction` guard the shared epilogue has always
  carried, so `database or disk is full` reaches the operator intact. This is
  not a module sweep: `open()`'s schema-creation rollback has the same shape
  and is left alone here, because correcting it would change another path's
  error identity. #715 owns it.
  #713 scoped this to the claim path, on the reasoning that the sibling's
  `COMMIT` is the last statement in its `try` and its handler is therefore only
  ever reached with the transaction still open. That holds for a failure after
  the commit but not for one during it: a failing `INSERT` reaches that handler
  with the transaction already gone, and the sibling masked the disk failure
  identically. Both are fixed, and the ticket's note recording the sibling as a
  verified non-defect is corrected. The claim's post-`COMMIT` statements also
  move out of its handler, and a claim whose `ProviderRecovery` cannot be built
  now stays committed: the lease is the recovery, because the claim query
  reclaims a `checking` row once `claim_expires_at` passes, where a
  compensating write after the commit would add a failure nothing could handle.
  Reaching that path at all needs a row the health table's `CHECK` constraints
  admit and the dataclass rejects — an identity carrying an embedded NUL
  qualifies, since SQLite's `length()` stops counting there and Python's `len()`
  does not — and no method on the store writes one, so the cost is one lease
  period per row some other writer put in the table. (#713)
- State which lines the code-economy consolidation test measures, so two
  readers measuring the same change reach the same verdict. #692 required a
  consolidation to be lighter than the copies it replaces without saying whether
  the shared module's header, imports and declaration are part of "it", and #707
  hit that for real: a reviewer read the clause as covering the whole module,
  the change read it as covering the implementation, the text supported both,
  and the same two consolidations passed on one reading and failed on the other.
  The clause now names what its test reads — all the implementation a
  consolidation adds, shared and forced on the former call sites alike, against
  all the implementation it deletes — and leaves structure, and documentation a
  governing issue's acceptance demanded, in the reporting buckets the same rule
  already requires. Structure costs the same for a good abstraction and a bad
  one, so charging it to the abstraction judges the module boundary rather than
  the abstraction, and rejects the small cross-file consolidations the rule most
  wants, every one of which needs a shared home. A physical line is
  implementation wherever it carries an executable expression, even where it
  also carries a declaration, so an expression-bodied helper cannot book itself
  as structure by fitting on one line.
  Settling this does change verdicts, and it changes exactly the two the
  ambiguity was found on: `+7` and `+16` fail on the module total the old text
  equally supported, and pass under the amended clause. What has not moved is
  the test's severity. A shared implementation fatter than the copies still
  fails, so does a thin one that pushed its bulk out to the call sites, and the
  generalised helper carrying a configuration switch for each former call site
  is named as the case the rule exists to stop. #662's two consolidations are
  worked into the clause with their line counts, and its entry reconciles every
  bucket of both, so the measurement basis is not rediscovered by argument a
  third time. (#708)
- Make the Library work card's attention line agree with itself. The noun
  pluralised and the verb did not, so a work with one open field read "1 field
  need attention". It now reads "1 field needs attention", and two or more
  still read "2 fields need attention". No assertion covered the string, in
  any tier, which is why three green tiers passed over it; one now pins both
  forms. (#705)
- Make the review-web test double produce the Attention row the repository
  actually produces, and delete the two `len(row)` guards it was propping up.
  #668 kept those guards because removing them failed nine tests whose stores
  reported only four of the six declared columns -- but those stores are test
  doubles, not a second real producer, so the guards were protecting the
  fixtures rather than the product. The shared double now emits the automatic
  outcome and the cause, keyed by destination so one work's rows carry
  different values, and the six paging fakes that assert nothing about either
  column carry them as `None`. The Attention reader unpacks the declared row
  directly. A new test drives a work whose title field is suspended for
  automatic safety, whose series field has an automatic abstention, and whose
  subtitle field has neither, and pins the three different classifications the
  owner is shown; with the double back at four columns it fails. The double's
  row list is annotated with the declared row type, because mypy resolves the
  bare `tuple(...)` return against the declared type and reports a narrowed row
  as `arg-type`, which the `tests.*` override disables. No product behaviour,
  SQL, or projection changed. (#703)
- Give the duplicated provider and review-surface logic one home each, and
  record where an abstraction would have cost more than the copies it removed.
  Four consolidations landed and two were declined on measurement.
  The provider-recovery store repeated one commit/rollback epilogue in six
  write paths byte for byte. A `_rollback_on_failure` context manager replaces
  them, and the module's other eleven epilogues keep their longhand because
  they admit `ProviderRecoveryError` or `AttributeError` as well: widening the
  shared handler to swallow them would change which failures the six report as
  a store failure. Two tests now pin the epilogue's own behaviour -- a failing
  `COMMIT` rolls back and reports persistence unavailable with the sqlite error
  as its cause, and a store error raised inside the transaction rolls back and
  keeps its identity -- forced through a connection substituted at
  construction. `-23` lines.
  The three metadata adapters each declared an identical sixteen-line
  `_ProviderFailure`; `retry_safety.ProviderFailure` is now the one identity,
  with every per-provider status, category and bound unchanged and still pinned
  by the adapter suites. Google Books' `_record_attempt` also took a
  `retryable` argument its body never read, computed at six call sites;
  removing it leaves all three adapters' attempt recording byte-identical.
  `-24` lines.
  Consolidating that attempt recording is declined, with the measurement rather
  than an assertion: a shared `record_provider_attempt` was built, made
  lint-clean, type-clean and green, and measured 5,869 lines across the four
  modules against 5,849 for the longhand. Because each adapter would still need
  a forwarding method to reach its own name, attempt count and record list, the
  abstraction removes no copy and costs 20 lines. The longhand stays. Moving
  the attempt log into a shared aggregate would be lighter, but it rewrites
  attempt accounting across three adapters, which this change must leave
  untouched; it is recorded, not attempted.
  On the review surfaces, four route modules formatted the same server value
  four ways, so `media_kind` reached the owner as `Media Kind` on Activity,
  `Media kind` on Library and Attention, and `media kind` on Detail's field
  tabs and headings. `frontend/src/labels.ts` settles it on sentence case --
  the convention three of the four already used, and the one that does not make
  a server enum look like a proper noun -- with `labelWords` for a label set
  inside a sentence, which two surfaces were deriving by hand. Activity's
  destinations and reasons lose their title case, Detail's field tabs and
  headings gain a capital, and Library's `Choose a … value` control loses a
  mid-sentence one; Attention and the rest are unchanged.
  The two cover layouts, which must stay visually distinct and do, were also
  deriving their data identically: one image URL template character for
  character, one failed-image set, one prepare mutation. `useCoverChoiceData`
  owns those three and no markup moved. The idempotency key both review routes
  generated now lives beside the controller that puts it on a command.
  All three browser changes are consolidations, measured on all the
  implementation each adds against all it deletes. `labels.ts` adds 3 lines --
  `fieldLabel`'s body, `labelWords`, and the `opaqueSource` pattern -- against 8
  deleted with five formatter declarations and two `opaqueSource` copies, and
  forces no plumbing at the call sites. `useCoverChoiceData` adds 17, plus one
  `const cover = useCoverChoiceData(reviewCaseId)` in each of the two routes,
  against the 20 they delete: 19 against 20, which passes by a single line and
  is the thinner case of the two. The idempotency key is lighter outright.
  The net figures reconcile bucket by bucket, which the rule requires and the
  two commits' own messages did not do. `labels.ts` to `+7`: `+3` implementation
  and `+1` structure in the new module, `-8` implementation and `-5` structure
  deleted with the five declarations, `+7` for one consumer import per module,
  `+9` for the documentation this issue's acceptance demanded, and 63 call sites
  rewritten in place for 63. The cover module to `+16`: `+17` implementation and
  `+2` call-site plumbing against `-20` deleted, `+7` module structure, `+5`
  comment, `+2` consumer imports, and `+3` where Prettier re-wrapped Detail's
  prepare button around the longer expression. These figures supersede the
  per-bucket splits in `0dd02ffb` and `ce353997`, which reported the right net
  deltas over incomplete buckets.
  Review first read the two browser modules as relocations. That was wrong and
  was withdrawn there -- every cross-file consolidation needs a shared home, so
  the reading would have swallowed the rule it was excusing them from -- and
  #708 now states in the rule itself which lines the test measures. Six frontend
  assertions moved with the copy the casing convention deliberately changed; the
  suite stays at 149. (#662)
- Keep installed pytest plugins and oversized parameter names out of the test
  tiers. Two further defects in the engine adoption, both found in review of
  #661.
  Rejecting `PYTEST_PLUGINS` closed one door and left the next one open: pytest
  auto-loads every installed distribution exposing a `pytest11` entry point, so
  a `pytest-randomly` or `pytest-xdist` in a contributor's environment could
  reorder, shard, or drop cases with no environment variable set and nothing
  rejected. A tier now passes `--disable-plugin-autoload` and loads pytest's
  builtins and the runner's own plugin only. Cohort membership is unchanged:
  the two distributions currently installed, `anyio` and hypothesis's pytest
  integration, contribute no cases, and the suite's hypothesis tests pass
  without the integration because they run through `unittest.TestCase`.
  A long parameter set produced an identity past the stall diagnostic's
  512-character safe bound, so the diagnostic was withheld from exactly the
  parameterized style this milestone's conventions recommend. The parameter
  component is now bounded, with its retained head paired with a digest of the
  exact parameters rather than truncated: bare truncation would map distinct
  parameter sets onto one identity and make the collision guard refuse a
  legitimate cohort. Module, class, and function names are left verbatim, so a
  `unittest.TestCase` identity is still exactly what `TestCase.id()` produces.
  (#661)
- Stop the test-tier runner honouring ambient pytest configuration, and give
  it unittest's accounting for shared setup, unexpected successes, and native
  case identities. Four defects in the engine adoption, all found in review of
  #661 and all about the runner being the gate.
  `pytest.main` incorporates `PYTEST_ADDOPTS`, so `PYTEST_ADDOPTS='-k passing'`
  reduced a cohort to its passing subset, returned success, and reported the
  tier **passed** with a failing test sitting unrun beside it -- the gate could
  be disabled from outside and still report green, and the exact-artifact
  qualifier preserved the variable into its isolated environment. Ambient
  `--capture=fd` would additionally have silenced the stall diagnostic. A tier
  now refuses to run under any pytest variable that can reach collection,
  execution, plugin loading, or the diagnostic streams, naming the offending
  variables and never echoing their values, which may carry private paths.
  `PYTEST_THEME` and `PYTEST_THEME_MODE` stay permitted and are documented as
  such: they only colour a stream every tier already discards.
  A `setUpClass` failure made the tier's machine-readable evidence least
  accurate exactly when shared setup broke: pytest reports the failure against
  every collected method, so an unexecuted two-method class read as two tests
  run and two errors where unittest reports none run and one class-level
  error. Class-scoped setup and teardown failures are now counted once, under
  a `setUpClass`/`tearDownClass` identity, while an instance `setUp` failure
  stays one error per case.
  A non-strict `xfail` that unexpectedly passed left pytest's exit code at
  success, so the tier reported passed while the runner's own accounting held
  an unexpected success. A tier now passes only when the engine and that
  accounting agree, and `xfail_strict` is declared so a native `xfail` fails at
  the engine too.
  A native pytest case had only its node id, which carries the file path and,
  when parameterized, bracketed parameters. That id could never match the
  stall diagnostic's safe-identity guard, so a native test running past the
  threshold produced no diagnostic at all -- and the identity was not path-free
  either, so a failing native case would have reported a filesystem path.
  Native identities are now built like unittest's: dotted, path-free, and
  reduced to the safe alphabet, with a cohort whose reduced identities are not
  unique failing closed rather than silently shrinking its manifest. (#661)
- Split the router tests by route and record the suite's standing test
  conventions. `frontend/tests/router.test.tsx` held 124 of the frontend's 149
  tests in one `describe` across 2,710 lines, with its first 108 lines given to
  fixtures and its first four tests exercising pure functions of other modules.
  Five route modules now own the route tests -- library, activity, detail,
  policy, and shell -- none over 793 lines; the four pure-function tests move
  beside the modules they prove; and the fixtures move to
  `frontend/tests/fixtures.tsx`. The count is unchanged at 149 with an
  identical leaf-title multiset: 124 tests relocated, every one a 1:1 move.
  The split exposed one position-dependent test. `polls idle Activity while
  mounted and tears the poll down after navigation` passed when it ran after
  forty-four siblings and failed when it ran first, because awaiting the
  router's `navigate` resolves the router but not React's passive-effect
  cleanup -- and it is that cleanup which unsubscribes the Activity query
  observer and clears its poll interval. On a cold module the observer was
  still subscribed when the test measured, so an interval that should have been
  gone fired once more. The test now waits for the observer count to reach zero
  before measuring, asserting the teardown it names instead of assuming it.
  `docs/development/testing-strategy.md` gains a `Test economy` section stating
  three permanent rules, in the manner of `AGENTS.md`'s `Code economy`:
  parameterize variants rather than copying methods, with case count as the
  invariant; build fixture data through shared builders rather than inlining
  the same payload, and never import another test module's fixtures; and force
  a failure through a seam that survives relocation, because a member
  substituted on the object under test stops reaching its target the moment
  that member moves, and the test then passes while testing nothing. Twenty-two
  sites of that last shape exist today and are recorded for separate work.
  (#661)
- Split the four oversized backend test files into per-concern modules. The
  suite's four largest files -- 21,204 lines and 337 cases between them --
  each held one or two classes covering unrelated surfaces, so a failure
  reddened a 5,000-line file instead of naming what broke.
  `test_metadata_adapters.py` (6,184) becomes six modules, `test_proposal_set.py`
  (5,527) six, `test_metadata_report.py` (5,213) five, and `test_review_web.py`
  (4,280) four. No resulting module exceeds 1,413 lines.
  Three of them doubled as the suite's fixture library: nine modules imported
  proposal builders out of `test_proposal_set`, seven imported a review-case
  report out of `test_review_case_persistence`, and a browser-tier module
  imported a test double out of a fast-tier one. Those builders now live in
  `tests/support/`, so no cohort reaches into another cohort's tests for them.
  Every case body is copied verbatim from its source's own lines: no assertion
  is added, removed, reworded, or weakened. The 337 cases remain 337, and each
  moved identifier is a 1:1 relocation -- the removed and added leaf-name
  multisets are identical per commit and across the packet, so no case
  disappeared and none was invented. Browser, slow, and required-installed
  manifests are unchanged. (#661)
- Run the public test tiers on pytest without changing what a tier is.
  `scripts/run_test_tier.py` hand-rolled `unittest` discovery, which foreclosed
  fixtures, `parametrize`, and assertion introspection for every future test in
  the suite. Collection and execution now delegate to pytest while the tier
  contract stays exactly where it was: the same `COHORTS` patterns, the same
  dotted path-free test identities, the same `test-cohort-v1` manifest, the
  same `test-tier-stall-v1` diagnostic on standard error, the same
  `test-tier-result-v1` summary with #655's bounded failure tracebacks on
  standard output, and the same fail-closed behaviour for an empty or
  unimportable cohort. Every cohort manifest and `test_ids_sha256` is
  byte-identical to the value the unittest engine produced -- fast 1,631 cases,
  browser 6, slow 2, required-installed 15 -- and no test file changed to make
  that true.
  Two engine settings carry tier behaviour rather than taste. `--capture=sys`
  replaces only `sys.stdout`/`sys.stderr`, exactly as the runner's own
  redirection already did, so file descriptor 2 keeps pointing at the real
  stream and the stall diagnostic still reaches an observer; descriptor-level
  capture, pytest's default, swallows it silently. `--tb=native` keeps the
  interpreter's traceback format, so a traceback truncated to its retained tail
  still ends at the assertion and the innermost frame.
  The runner also implements unittest's `load_tests` protocol, which pytest does
  not. `tests/slow/test_continuous_review_scale.py` uses it to select one method
  from a subclass of a fast-tier base; without that handling the slow cohort
  would have grown from two cases to six and would have re-run two fast-tier
  cases under a second identity, breaking tier disjointness rather than merely
  the digest. (#661)
- Distinguish consolidation from relocation in the code-economy rules, and let a
  single-consumer helper earn its place, so the requirement that an abstraction
  be lighter than what it replaces stops being read as a bar on moving code to
  where it belongs or on naming something worth naming. The rule was added
  alongside a shared validation vocabulary and has since correctly deleted five
  single-consumer helpers; it also drew six review findings against structural
  work whose entire purpose was relocating logic across a layer boundary, which
  necessarily adds a module header, imports, and explicit parameters for what
  were implicit captures. The consolidation rule is unchanged. Relocation is
  now judged on whether the code lives where its dependencies allow and whether
  the move duplicated anything, and stands on correct ownership however few
  lines it carries. Consolidation keeps its requirement to be lighter than the
  copies it replaces. A helper with no caller is still deleted, but one with a
  single caller extracted within a boundary may be kept where it earns its place
  by carrying a meaningful name, exposing a directly testable contract, or
  creating an enforceable boundary — stated in the change rather than assumed.
  Refactoring pull requests attribute their line delta to moved code, structure
  the change required, copies consolidation removed, and checks newly added,
  which reconcile to the net figure. (#692)

- Make four stale type declarations state what the code actually produces and
  consumes, and delete the casts that carried the difference.
  `ReviewAttentionSnapshot.rows` claimed four columns where the repository has
  always returned six, which made the type checker prove the Attention
  reader's two trailing columns permanently absent -- the automatic outcome
  and the cause that decides whether a suspended field is shown as needing
  review. `ReportRepository.publish` declared its `after_publication` hook to
  receive the repository, while both adapters declared the same parameter as
  `object`; parameter types are contravariant, so the adapter accepted fewer
  callables than the contract promises and `SQLiteMetadataReportRepository` did
  not structurally satisfy the protocol it implements. Twelve members of
  `ContinuousFieldEffectRepository` were declared `*values: object`, so the
  seam through which every consent grant, manual hold, capability suspension
  and cover retirement passes constrained neither argument count nor argument
  type. And `field_effect_authority_source` iterated a review case's
  `target["fields"]` projection before checking it was a list, so a
  projection that is neither a list nor iterable raised `TypeError` out of the
  repository instead of the `authority_binding` failure its caller handles;
  every projection the old order could classify is rejected identically. Four
  casts and two block comments recording the mismatches are deleted, and
  strict mypy proves what they asserted. No wiring, message, or runtime output
  changed. The two `len(row)` guards in the Attention reader stay: removing
  them fails nine existing tests whose stores report only the four columns, so
  each still protects a short row. The fifth declaration in this report, three
  `from_json` classmethods returning `Self` without constructing through
  `cls`, was already corrected in #666 itself and needed no change. (#668)

- Declare the automatic-safety seam the served composition already binds.
  `compose_review_application` builds the policy notebook and the owner-only
  automatic-safety controls from one `policy_store`, but
  `AutomaticSafetyControls` accepted `object` and cast to the protocol it
  names, so no signature anywhere could catch a store that lacked the safety
  members. A miswiring surfaced only at runtime, as "automatic safety is
  unavailable" on every safety read, pause, suspend, and recover -- a
  capability outage wearing a transient-storage message. That is not
  hypothetical: it shipped during #658 and was caught in review. The composed
  parameter now names both contracts it is bound to. No wiring, message, or
  behaviour changed; the store passed there already implements both seams.
  (#667)
- Compose the `policy-preview` CLI only from a contract its repository
  satisfies in full. `run_policy_preview` handed `SQLiteShadowPreviewRepository`
  to six `compose_review_application` parameters it does not implement -- every
  session, review-case, decision, library, browser-state, and policy-mutation
  member -- behind six `# type: ignore[arg-type]` and a `LATENT DEFECT`
  comment. The preview was correct only because it called two of the operations
  composed there; any other one was a runtime `AttributeError` waiting for a
  caller. The preview now composes a preview-scoped application over one
  read-only store contract, so no session, review-case, decision, library,
  browser-state, or policy-mutation operation exists on it to reach a member the
  repository lacks. All six suppressions and the comment are deleted and strict
  mypy stays clean without them.
  The narrowing is static only: the same repository object is passed, nothing is
  wrapped or forwarded at runtime, so no capability can be lost the way a
  runtime facade silently removed automatic safety during #658. The preview
  boundary stands alone rather than sitting under the served application, and
  the three rules both paths need -- the automatic-qualification rule, the
  public F20 disposition guard, and the named-library invariant -- moved into
  this module's shared validation vocabulary instead of being copied, so no
  rejection rule or its message exists in two places.
  `policy-preview` output, exit codes, and failure messages are byte-identical
  across thirteen success and failure scenarios, and the database is unchanged
  in every one. (#667)
- Move review-pipeline construction, provider-recovery activation, and policy
  resolution out of the composition root and into owned application modules.
  `bootstrap/composition.py` was 2,418 lines in which the largest function,
  `create_review_pipeline`, was 558 lines of decision logic wearing a bootstrap
  hat, and the layer-boundary test constrains bootstrap to nothing, so nothing
  arrested the drift structurally. Four application modules now own it:
  provider-recovery evidence (which terminal statuses become durable health,
  which lanes become retry work, how a raised failure is classified for
  backoff), provider-recovery activation (one claimed retry member and the
  durable cutover outbox), the nine review-pipeline stage decisions together
  with the pipeline they assemble, and current-policy resolution beside the
  other policy seams. All of it gains its destination layer's import
  constraints, which is a strengthening.
  Nothing about wiring changed. The composition root still constructs every
  adapter, reads every secret, and opens every connection; the moved code
  receives those as explicit dependencies. The recovery-classification table is
  identical across all 38 inputs, policy resolution is identical, the public
  surface is unchanged, and no configuration value, scalar, or collaborator type
  reachable from a composed pipeline or a composed server was lost. (#660)
- Decompose the 1,131-line `_package_report` EPUB extractor -- the largest
  function in the codebase and the one that parses the most hostile input in
  the system -- into twenty-nine named stages over four named state types, each
  stage under 150 lines and each carrying one documented contract.
  `_package_report` is now a linear
  composition whose call order *is* the acceptance order, so what the extractor
  accepts, rejects and bounds can be read in one place instead of inferred from
  a 285-branch function. Each stage now has direct unit tests for the hostile
  inputs previously reachable only through a whole archive: malformed XML,
  unsupported versions, duplicate and ambiguous ids, refinement cycles,
  container path escapes (`../`, `..%2f`, `\`, absolute), oversize references,
  unsafe projection text, media-overlay and fallback abuse, and every
  evidence-bound that must reject rather than truncate. Every one of the
  twenty-nine stages is invoked directly by that suite, not only through a
  parent stage. Two hypothesis properties cover the reference guard and the
  prefix vocabulary; each pins both of its branches with explicit `@example`
  inputs and then asserts that those exact inputs produced the recorded
  outcome, so the guard fails when the behaviour regresses or the recording
  breaks, and never merely because a seed explored the search space
  differently.
  Nothing about extraction behaviour changed, and that was proven mechanically
  rather than argued: a corpus-digest gate captured a canonical digest of every
  `EpubPackageReport` the existing suites produce, plus the outcome and message
  of every rejection, and required exact equality after every commit. The gate
  was first proven able to fail -- a deliberately perturbed warning citation
  passed the entire existing test suite while the gate caught it. (#659)

- Split the 9,943-line SQLite metadata-report repository along its aggregate
  seams. Persistence is now nineteen focused modules over one shared
  connection, transaction and schema spine: each owns one aggregate's tables
  and validation, and no statement outside a module writes the tables that
  module owns. `metadata_reports.py` keeps the factory, the read-only shadow
  preview reader, and the compatibility surface every existing caller imports,
  so no import statement, application port, or call site was rewritten, and the
  composition root still wires the same object into the same protocol-typed
  parameters it always did.
  One behaviour deliberately changed, and only one. Transactions were
  hand-rolled at 56 separate `BEGIN IMMEDIATE` sites, of which exactly one
  handled nesting; a cross-module call inside an open transaction would either
  raise or, worse, silently commit the enclosing caller's partial work. All of
  them now share one re-entrant scope that uses a savepoint when a transaction
  is already open, so a nested repository call undoes only its own writes.
  Every `except` block, all 123 `sqlite3.Error` handlers and all 365 distinct
  error messages, stayed verbatim at its own site, so which error surfaces from
  a failure is unchanged. Everything else is mechanically equivalent and was
  proven so after every commit: a freshly created database is byte-identical by
  whole-file `sha256`, its 119 `sqlite_master` rows are unchanged, the multiset
  of SQL string literals across the package is unchanged after the transaction
  commit, and 120 forced-failure captures produce the same exception type and
  message as before. (#658)

- Express field validation once, as a shared vocabulary applied declaratively,
  instead of expanding the same checks longhand for every field. A new
  dependency-free `mechanical_librarian.domain.validation` module names the
  house conventions — exact-type checks that reject subclasses, bounded
  integers, pinned constants, and closed-object field sets — as predicates that
  introduce no failure mode of their own, so a caller keeps its own error
  identity, message, and short-circuit order. A closed-object schema propagates
  whatever a caller's own field check propagates, which is what lets a longhand
  `value not in FROZEN_SET` convert to that set's own membership test without
  changing when it raises. The persisted field-effect state validator now reads
  as eight declared document schemas rather than a
  sequential wall of per-field conditions. Nothing changes about what is
  accepted or rejected: the same inputs fail with the same errors, proven by
  the existing suites passing unmodified. In the browser, the seven responses
  that are checked only for their schema version now say so through one named
  seam instead of seven separate double casts, and a case-detail field that is
  not an object, or that omits `current` or `proposed_values`, is now rejected
  as an invalid response rather than surfacing as an unhandled `TypeError`.
  (#663)

- Report a malformed bibliographic combination as a packet error rather than a
  `TypeError`. A combination whose `source_representations` mixed strings with
  other types was compared against its sorted set before its members were
  checked, so ordering the mixed list raised `TypeError` out of the packet
  boundary instead of the packet's own "shadow-review packet is invalid" error.
  The member check now runs first. Nothing changes about what is accepted: a
  list of only non-string members was already rejected by the representation
  identity pattern, and this was the wrong exception type rather than a hole.
  (#669)

- Fail closed on two malformed inputs the boundaries that own them were meant
  to reject. A shadow-review packet now proves that every member of a correlation's
  `candidate_ids` and `proposal_ids` is a string, the way the packet's other
  identity lists already do. A correlation whose identities were all of one
  non-string type was accepted outright, and one with mixed types left the
  packet constructor as a `TypeError` instead of the packet's own
  `ShadowReviewValueError`; decoding a packet with `from_json` already
  translated that `TypeError`, so only direct construction saw the wrong type.
  Both malformed shapes now raise the existing "shadow-review packet is
  invalid" error, and no packet that validates today is rejected. The
  `provider-recovery` command now refuses an absent review configuration with
  the same `review configuration is unavailable` configuration error the
  `cover-review` and `review-activity` commands already raise, instead of
  composing the review pipeline and failing with an `AttributeError` on the
  absent configuration. No new error identity or message was introduced and no
  other caller of the pipeline changed. (#669)

- Report a failed field-effect claim as what it was, so `claim_conflict` again
  means an actual concurrency conflict. Claiming a plan can fail because the
  control table is paused, the capability is suspended, the authority or
  consent was revoked, the row moved under an optimistic state check, or
  storage faulted; every one of those was previously reported to callers,
  history, and the API as `claim_conflict`. Each now surfaces under the
  repository's own code — `paused`, `capability`, `authority`, `consent`,
  `claim_conflict`, or `persistence` — and an unrecognised claim failure
  surfaces as `claim_failed` rather than borrowing one. Some failures therefore
  reach the API with a different code and status than before: a genuine
  conflict and a paused control table stay 409, while a suspended capability, a
  revoked authority or consent, and a storage fault now return 503 instead of
  409. The cover-recovery cleanup that runs after a lost claim no longer
  swallows its own failures: a plan row that has genuinely disappeared stays a
  benign outcome, but a storage or filesystem fault during cleanup is reported
  as `cover_recovery`, chained onto the claim failure that provoked it so
  neither is lost. Every path that raised before still raises, still as a
  guarded error, and the cleanup still attempts removal in exactly the same
  circumstances. (#656)

- Report known-vulnerable Python dependencies in CI, so the backend half of the
  supply chain is audited on every change the way the frontend half already
  was. A `dependency-audit` job in the public gate scans the complete `uv.lock`
  closure — runtime dependencies and the development group together, because
  CI installs both — with a digest-pinned OSV-Scanner on every pull request and
  every push to `main`. Advisory data is fetched at run time, so the pinned
  scanner still sees current advisories, and no package is added to the project
  to run it. The only mechanism that may hold such a failure open is the
  time-boxed advisory acceptance register, `osv-scanner.toml`: one named
  advisory, one stated reason, one expiry date, and no severity floor, package
  exclusion, or repository-wide loosening. The scanner stops filtering an
  advisory once its date passes, and the required `fast-source` check
  independently fails an entry that is already expired, runs more than 90 days
  out, carries no substantive reason, or introduces any other configuration
  table. The register is empty: the advisories the audit reports today are
  tracked for repair, not accepted. The audit reports on every pull request but
  is advisory rather than merge-blocking until a maintainer adds
  `dependency-audit` to the default-branch ruleset. Static analysis of this
  repository's own source and pull-request dependency review are deferred to
  #674, which is blocked on a repository visibility or plan decision, and
  nothing dormant was left behind for them.
  `docs/architecture/security.md` records the boundary, including what stays
  maintainer-manual. (#653)

- Verify merged `main` and the shipped container in the public gate, and make a
  broad test failure diagnosable. The gate now also runs on push to `main`, so
  two individually green pull requests that break `main` together are caught
  instead of merging silently; the concurrency group and every exact-revision
  reference resolve for both events, and a merged commit's verification is
  never cancelled. A fourth `container-image` job builds the shipped image with
  `docker build` at the exact revision, so a Dockerfile regression fails the
  gate rather than a maintainer's manual ritual; the built image is never run,
  loaded, or pushed, `compose.yaml` is not read by that build, and running the
  image's lifecycle qualifier stays maintainer-manual. The pinned Chromium
  payload is cached on the Playwright version and saved only after
  `verify_playwright_browser.py` accepts it, so an inexact payload is never
  persisted and every run still verifies. `scripts/run_test_tier.py` now emits
  each failing test's traceback on its narrative stream — bounded to the last
  4 KiB per test, and 256 KiB across the whole stream including its framing —
  and no longer truncates the failed-identifier list at 32. Raw cohort
  output stays discarded, and the `test-tier-result-v1` and `test-tier-stall-v1`
  lines are unchanged on standard error. (#655)

- Drive the Activity search debounce deterministically in the router test that
  covers draft and control focus across interleaved route completion. The test
  previously entered the query with real timers and settled the initial route
  load with a single zero-delay tick, so under load the keystrokes raced the
  post-load draft resynchronisation and the search value assertion failed in
  roughly a quarter of full-suite runs. It now waits for route-ready focus to
  complete before interacting and advances fake timers across the 150 ms
  debounce, matching the idiom already used by neighbouring Activity tests.
  Every assertion the test made is still made, and no product behaviour
  changed. (#661)

- Gate the Python backend on ruff and strict mypy in the public gate, with the
  configuration and the exact tool pins committed alongside the code they
  check, so `py.typed` and the `Typing :: Typed` classifier now describe a
  verified property. Every narrowing is explicit configuration with a stated
  reason and every site suppression is inline, named, and justified. Fixing the
  findings corrected one real defect: both shadow-review preview paths named
  `ShadowReviewValueError` in their `except` tuples without importing it, so an
  invalid persisted packet raised `NameError` from the handler instead of the
  intended `MetadataReportError`. (#652)

- Make a clean frontend checkout pass every documented source check without a
  manual ritual. `npm run typecheck`, `npm run lint`, `npm test`, and
  `npm run build` regenerate the gitignored DTOs first, so a fresh clone no
  longer reports misleading type errors; `frontend/prototype/` scratch space is
  excluded from lint; and `frontend/src` is Prettier-formatted with committed
  configuration that `npm run lint` enforces, so diffs, blame, and stack traces
  work at statement granularity. (#657)

- Keep retained metadata provenance at each Library review choice: current
  local presence, named corroborating sources, rationale, deterministic
  recommendation status, and limitations can be inspected in place without
  replacing the owner decision workspace. (#640)

- Pause only review-preparation work that shares an unavailable Audiobookshelf
  connection. The owner can run one authenticated, CSRF-protected bounded
  connection check; recovery resumes the retained checkpoint without rerunning
  completed packages or provider/proposal work. (#638)

- Run one configured, qualified cover-reviewer comparison during ordinary
  review preparation before the review package is published. Advice remains
  immutable, exact-current, and owner-only; unavailable or failed reviewers
  retain the manual comparison and never gain cover-write authority. (#600)

- Preserve valid unlabelled Hardcover work creators as neutral,
  identity-bound evidence and admit one deterministic authors proposal only
  when a distinct provider's complete explicit author set exactly corroborates
  the same qualified work. Partial, reordered, mixed-role, malformed, and
  same-authority creator sets remain unresolved. (#416)
  The exact-current metadata report and proposal policy contracts are now
  `metadata-report-v25` and `fixed-conservative-v8`; earlier development
  documents and databases remain incompatible under the clean-schema policy.

- Add durable provider- and configuration-scoped recovery canaries. Terminal
  packages register only retry-eligible lanes; the existing supervisor claims
  one exact-current canary before durable, independently leased sibling fanout.
  Shared failures publish nothing and retain bounded Retry-After/backoff;
  successful members complete ordinary proposal, admission, projection, and
  atomic package cutover while stale work and disabled configuration stop safely
  without target metadata writes.
  (#408)

- Add the internal, unreachable scoped provider retry-successor seam. It
  rebuilds only explicit retry-eligible failed lanes from an exact-current
  report, preserves unselected lane evidence, and relies on existing atomic
  review-package cutover; it adds no scheduler, endpoint, canary, or write
  path. (#629)

- Complete first-generation staged review projection before its package becomes
  owner-visible, while retaining published-package authority for Library,
  Attention, owner decisions, and field effects. (#630)

- Record terminal provider failure recovery eligibility as durable,
  configuration-generation-scoped waiting or dormant state. The local canary
  claim is non-executing: it makes no provider, replay, fanout, or write call.
  Operators increment `provider_recovery_generation` and restart after fixing
  a dormant provider condition. (#625)

- Make Library policy impact previews distinguish real changes from retained
  values, unsupported evidence, and owner review. Empty fields with no
  candidate now read `No change · remains empty — no supported value found`;
  exact-current, ignored, and abstained outcomes no longer inflate the change
  total, while bounded technical detail preserves their closed audit codes.
  (#609)

- Explain every closed Library policy representation in place: its admitted
  source/value, bounded transformation, field effect, and abstention boundary.
  Visible Google text now explicitly admits only eligible transformed Google
  Books text and never falls back to Hardcover; manual-review-only choices and
  any Automatic-to-Ask-me consequence are owner-visible. (#608)

- Record an equal automatic metadata candidate as a durable **No change
  required** Activity outcome without creating a guarded write plan or
  consulting write authority, consent, capability, backup, target, or read-back
  services. (#599)

- Show each Library policy chapter's exact changed-field count, including zero,
  with chapter-specific accessible labels. Unsupported fields now explain that
  they are read-only and cannot be changed in a draft. (#607)

- Keep authenticated Library and Attention reads bound to one completed review
  package. In-progress, stale, and failed preparation no longer replaces a
  visible prior package or exposes a first-generation partial result. Complete
  provider-lane failures and no-match outcomes remain inspectable without
  discarding coherent sibling evidence, while pre-package failures appear as
  bounded Library-only recovery states and never enter Attention. (#597)

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
  already observed ISBN-13 representative when one is available. Invalid
  identifiers and distinct ISBN families remain separate. (#595)

- Fix Linux SQLite connection validation to prove the exact opened `main`
  database path against the configured device/inode without relying on
  process-wide descriptor-number allocation. Authenticated review-preparation
  status polling now remains available and snapshot-consistent while legitimate
  concurrent writers churn rollback-journal descriptors. (#594)

- Add the protected owner-only Activity ledger. The calm current-state summary
  now leads to a server-paged, date-bounded record of durable review decisions,
  policy transitions, safeguards, guarded writes, and verification. Search,
  type filters, direct page navigation, bounded evidence inspection, and the
  existing consent, pause, revoke, stale, and ambiguous-recovery controls use
  the same authenticated private Activity projection; no queue, retry system,
  export, write authority, or persistence schema was added. (#591)

- Implement the protected Library policy notebook: one active policy and one
  mutable draft now present as four focused chapters, with field meaning,
  review mode, qualification, and representation kept together. A single
  whole-draft action validates, saves an immutable snapshot, and produces a
  current no-write impact preview before reversible, guarded activation.
  Preview rows sample across affected works and identify each exact field
  outcome against the generation-bound active resolution, while activation
  remains bound to the server-issued active-policy identity used for that
  preview, validates the complete success response, treats post-dispatch
  response-integrity failures as uncertain, retains reviewed authority while a
  prior transition is processing, and discards it after a currentness conflict.
  Keyboard-complete portal dropdowns, compact document flow, reduced motion,
  light/dark treatment, stale-preview invalidation, and explicit failure
  recovery preserve owner authority throughout. (#590)

- Implement the protected Library review workflow: wide entry now selects the
  first ordered reviewable work, retains a stable scrollable field rail, makes
  current-versus-exact-value choices one command, supports the protected cover
  comparison in Library, and presents a truthful review-complete boundary
  before deliberate next-work continuation. Recorded decisions remain separate
  from preparation, authorization, apply, and read-back verification. (#589)

- Activate owner-confirmed semantic cover decisions in the installed review
  product. One exact-current cover field now shows the incumbent and up to 12
  safe report-bound alternatives, runs the contained observer and deterministic
  policy as advisory evidence when configured, and lets the owner explicitly
  keep, select, or take no action. Immutable consideration, supersession,
  decision, and guarded application history bind exact source/original/preview,
  packet, identity, observer, schema, taxonomy, policy, target, and incumbent
  identities. Historical API, CLI, and React views reopen the bounded candidate,
  observation, policy, recommendation, and exact owner-choice context without
  restoring old image or write authority; the installed read-only Activity CLI
  exposes the same guarded plan, verification, stale, and recovery ledger
  without reading service credentials or probing Audiobookshelf. A
  selected original uses the existing verified Audiobookshelf
  cover effect path; observer failure preserves the model-free manual path.
  The retained versioned engineering benchmark remains a judgement-labelled,
  aggregate-safe proof rather than an unseen-generalisation claim. Observer
  execution releases the global application gate and revalidates the captured
  case before persistence; changed replay contracts and stale effects require a
  successor owner decision. Generic reviewer invocation and authentication
  remain source-workbench-only, and missing preview artifacts degrade through
  the typed unavailable response. (#501)

- Reconcile the 0.15.0 source-only semantic-cover workbench into one
  aggregate-safe maintainer review package. The retained real-provider corpus,
  deterministic image transport, conservative identity classifier, contained
  visual observer, and deterministic policy path now prove useful reusable
  benchmark behavior: 10/10 packets and 36/36 candidates admitted, 76/80 whole
  policy decisions exact, and zero safety or binding violations. Medium,
  promotional-text, and tie-in observations are strong on the benchmark;
  composition remains below the useful band. This is not an unseen-
  generalisation or runtime-activation claim, and #501 remains unpromoted.
  (#544, #500)

- Make source-only normalized cover transports byte-identical across supported
  platforms. Pillow remains the bounded RGBA decoder, while a small fixed PNG
  serializer now emits filter-zero rows, deterministic uncompressed DEFLATE
  blocks, explicit checksums, and exactly IHDR, IDAT, and IEND. This preserves
  packet-v2 source/transport separation, pixel and dimension equality, existing
  image and packet limits, and all reviewer authority boundaries. Exact
  single- and multi-block byte/digest fixtures replace the previous accidental
  dependence on the local Pillow/libpng/zlib writer. Retained replay produced
  exact macOS/Linux byte and digest parity for 147/147 affected previews and
  admitted the same frozen 10 packets/50 candidates on both platforms, with no
  provider or model calls and no production access or writes. (#542)

- Bind the public F20 candidate identity and terminal rich-field qualification
  statuses into policy validation, API, CLI preview, and the owner notebook.
  Field-option-local offline qualification admits all three series policies,
  both exact ISBN policies, genre fill/add/replace, tag fill/add/replace, and safe
  plaintext description to decision-only Automatic at 100% precision.
  Genre fill-empty is proved separately for missing, null, and empty target
  states, with 4/4 exact projections in each state; populated targets remain
  no-ops. Previously persisted F20 decisions retain validation only under the
  exact earlier digest and option set that originally authorized them.
  Each passing option now reaches the existing persisted-decision and guarded
  effect-plan path; multi-series proposals retain every qualified membership
  while Audiobookshelf receives the preferred first value. It adds no new
  write authority, keeps tags off by default, and leaves every other rich
  option manual. The pre-release shadow-outcome contract advances to v3 so
  databases containing incompatible v2 outcome identities fail cleanly at
  startup rather than during later
  reads. (#564)

- Record the F20 rich-metadata qualification candidate from 49 accepted
  real-work reports and 421 retained-response replays through the current
  offline pipeline. Qualification is field-option-local and does not require
  an unseen holdout. Subjective genre, tag, and description choices are scored
  on exact work-bound projection and safe operation rather than one golden
  semantic answer; all six genre/tag list operations and safe plaintext
  description pass,
  alongside three series and two ISBN options. Subtitle does not pass.
  `explicit` remains human-only, while narrators and cover remain excluded.
  (#565)

- Add the closed v2 rich-metadata policy vocabulary for manual preview: subtitle,
  series, exact list operations, bounded description text, and exact ISBN/ASIN
  choices. Rich-field automatic intent remains unavailable and this change does
  not add target or media write authority. (#566)

- Fix source-only contained cover-reviewer image transport so the Linux sandbox
  receives each rehashed, sealed preview through a descriptor-data mount instead
  of a `/proc` path mount. Oversized descriptor copies now fail before dispatch,
  and the gated child verifies and reopens each sealed image with an independent
  read cursor before Bubblewrap copies it. The correction preserves the existing
  image bounds, read-only sandbox files, gated dispatch, and absence of production
  authority.
  One retained real five-preview packet produced five admitted observations with
  zero mapping mismatch in 16.263 seconds under a bounded isolated profile.
  (#542)

- Add a source-only deterministic cover-policy report over exact packet,
  identity and closed observation evidence. Three typed booleans can avoid
  promotional text or tie-in branding and prefer minimal composition. Unknown
  and conflicting identities exclude only their candidate, so identity abstains
  only when no eligible candidate remains; unavailable, policy-relevant unknown,
  or unbound evidence still abstains or fails closed. The
  operation introduces no second model, provider, runtime, persistence or write
  authority. The historical 100-packet replay recorded 800 typed
  unavailable-observation abstentions. After observation transport and identity
  admission were corrected, a development reproof reused 50 retained packet
  observations covering all 148 candidates. Across all eight policies, exact
  acceptable-set, recommendation and whole-decision agreement were 88.5%, 89%
  and 86%; every policy combination remained at or above the predeclared 75%
  potentially-useful band, with zero safety or binding violations. A later
  engineering benchmark reused 10 independently reviewed complete packets with
  36 candidates and 80 frozen policy decisions. Diagnosis traced the earlier
  low coverage to Rust allocation aborts under a 512 MiB child limit; changing
  only that limit to 1 GiB moved an exposed development A/B from 4/8 to 8/8
  admitted packets. Under the corrected envelope the benchmark admitted all
  10 packets and 36 candidates without retry or violation. The baseline prompt
  was exact on 74/80 decisions but one combined policy reached only 7/10. One
  bounded composition-rubric clarification raised exact whole decisions to
  76/80, with every policy at 8/10 or better. Field agreement was 26/36
  composition, 33/36 medium, 36/36 promotional text and 35/36 tie-in branding.
  Policy admission now binds the authority-scoped reviewer profile and
  recomputes the exact observer reference, so a substituted profile or invented
  valid-shaped observer reference fails closed.
  This proves useful reusable-benchmark behavior, not unseen generalisation;
  composition remains the least certain field and had 12/36 independent human
  review disagreements. (#543)

- Recognize the one proven transient local contained-reviewer CLI abort without
  widening reviewer authority: only bounded strict pre-terminal JSONL on return
  code 134 with bounded nonempty stderr receives one fixed one-second retry in
  a fresh job and child process inside the same bound adapter and authentication
  lock. The unchanged request/profile/image/model/limit binding and value-free
  dispatch plus first-failure code/count/digest evidence are retained; every
  other failure remains terminal. Frozen v1 observations remain admitted only
  under their exact original instruction identity. (#543)

- Reduce private Actions consumption without recombining the qualification
  suites that previously timed out: run the three exact-head pull-request jobs
  only for candidate commits, remove the runner-only aggregate and post-merge
  duplicate, and retire the remote linked-issue runner in favour of the trusted
  controller's exact closing-reference and merged-tree proof. (#571)

- Add a source-only cover-observation report with one deliberately small,
  policy-neutral visual vocabulary. Each closed observation binds the exact
  report packet, candidate, preview digest/variant, taxonomy, instruction,
  schema, and contained observer identity; invented, duplicate, hostile, or
  unbound results fail closed. The workbench-only proof adds no runtime,
  provider, production-library, persistence, ranking, or write authority.
  Independent blinded reference judgements are not claimed human ground truth.
  The first 50-case development configuration reached 84–98% per-field exact
  agreement and a 20/20 exact shuffled repeat, so no further configuration was
  run. Its minimal-positive precision was 11/14 (78.57%) and reference-minimal
  recall was 11/15 (73.33%), a mixed and sparse result. A fresh one-shot final
  cohort admitted 48/50 candidate rows, reached 83.33–97.92%
  per-field agreement and 75% all-four agreement, and retained two
  post-dispatch, pre-admission failures without retry or safety/binding
  violation. Their exact shared cause and bias magnitude are unknown. The 4%
  candidate missingness rate remains within the frozen 5% candidate-coverage
  tolerance and does not change overall per-field sensitivity bands, but the
  75% joint admitted-case agreement
  falls to 72% and is not robustly useful when both missing results count as
  disagreements. The 50 selected rows came from 29 packets and rows within a
  packet are correlated; aggregate receipts cannot establish distinct-packet
  dispatch coverage. Composition's 83.33% overall agreement masks only 15/21
  (71.43%) minimal-positive precision, so prefer-minimal usefulness remains
  unproven. Tie-in's 95.83% overall agreement also masks only 2/4 (50%)
  branded-positive recall against 44/44 absent-case specificity, so avoid-tie-in
  policy usefulness remains unproven.
  (#541)

- Preserve bounded rich metadata evidence for manual review without widening
  automatic authority. Advance the exact-current shadow-review packet to v6
  so typed taxonomy citations cannot be confused with the prior packet shape.
  (#563)

- Add a source-only, report-local cover identity projection that classifies each
  retained cover as verified manifestation/work, plausible, unknown, or
  conflicting from bounded retained evidence before aesthetic or policy work.
  The canonical report binds opaque report, packet, candidate, classifier, and
  evidence references; explicit identifier and edition/work contradictions plus
  incompatible manifestation publication/format evidence win. Exact
  report-and-packet-bound EPUB covers verify their source manifestation only;
  an unresolved provider cover is merely plausible when its exact edition/work
  pair is retained by an ISBN lane (or an unmapped frozen legacy discovery
  report), with retained title agreement and no unopposed creator/contributor
  disagreement or deterministic
  work contradiction. Exact retained creator/contributor overlap counts as
  support and missing creator values are neutral; pairwise disagreement
  alongside agreement and edition-specific disagreement do not
  erase title support, and plausible never means
  verified—without provider calls, visual inference, persistence, or write
  authority. Exact report-bound reference and provenance checks reject
  digest-equal foreign packets; edition-specific date evidence neither supports
  nor vetoes plausibility, and default work covers retain their conservative
  identity meaning. (#540)

- Polish the authenticated interface without changing its authority: restore
  the asymmetric book-spine shell mark and touch-safe secondary actions; make
  Policy a one-chapter-at-a-time notebook with changed-field emphasis and an
  explicit validate, preview, impact, and activation cadence; give the full
  current-review route a compact field rail and dominant two-column decision
  canvas; and lead Activity with current recovery and recent outcomes while
  keeping write consent in a reachable disclosure. Direct and native Activity
  entry remain inspectable in the installed application. Root entry now routes
  to sign-in, while authenticated review deep links and refreshes render the
  application shell so its bounded missing-case recovery can run. Policy's
  cadence now reports checked, saved, previewed, inspected, and activated state
  truthfully; compact review navigation cannot remain sticky beneath a wrapped
  masthead; and collapsed Activity consent counts only libraries whose exact
  backup, capability, destination, and prepared-work gates permit action.
  (#555)

- Restore the Variant M Library review workspace: one permanent Library
  destination with a paginated, recoverable URL-owned Attention queue, direct
  current-or-specific value decisions, authoritative shared settlement, bounded
  readable evidence, cross-work continuation, and a responsive task flow.
  Complete the selected-work semantics with current-value field position,
  inspectable automatic and recorded siblings, a structured multi-provider
  source/observation/basis ledger, explicit external-library capability context,
  richer queue rows, Previous/Next movement, and exact-command explicit retry.
  Wide layouts retain Library, decision, and optional attention together;
  narrower regular layouts retain Library and decision with an attention
  drawer; phone layouts stage the same information without dropping it.
  Original actionable-field positions do not collapse as decisions settle,
  intermediate widths keep the work index beside selected detail, the queue
  drawer clears the wrapped shell, and retained provenance stays visible when
  supplemental evidence fails.
  Superseded cases never expose decision controls; work and field position carry
   into full review; direct review entry restores focus to the selected field.
   The synthetic qualification cohort contains 20 public-domain works across
   local, Project Gutenberg, Google Books, and Open Library shaped evidence.
   (#554)

- Admit deterministic report-bound cover packets to the source-only contained
  reviewer with opaque digest/order identity, incumbent-preserving deduplication,
  fixed 12-preview/1-MiB-each/8-MiB-total limits, read-only ephemeral image
  arguments, typed cleanup, and no runtime, production-library, or write
  authority. (#542)

- Normalize bounded report-bound RGBA cover pixels into deterministic,
  metadata-free PNG transports before isolated review. Source and derived
  identities remain separately bound; malformed, foreign, changed, oversized,
  or non-canonical inputs still fail closed, with no provider, runtime, or write
  authority. (#542)

- Reconcile and qualify the complete 0.14.1 authenticated interface against its
  exact surface-specific references. The packaged source, wheel, and container
  retain one persistent responsive shell, direct current-or-specific-value
  decisions, the refined Policy notebook, and the calm Activity ledger without
  expanding metadata or write authority. (#534, #499)

- Restore Activity as the calm, readable guarded-work ledger: explicit
  consent/readiness with backup explanation, readable current-report work and
  author identity, continued history during partial consent, scoped pause and
  revoke controls, and exact grouped recovery without queue, retry, or invented
  progress authority. (#533)

- Restore Policy as the refined semantic notebook with Identity, Publication,
  Curation, and Unsupported content chapters; synchronized sticky and compact
  navigation; readable posture, representation, qualification, impact, and
  active-versus-draft state; and an explicit activation review over the existing
  guarded currentness-bound operation, with contained and returning keyboard
  focus. (#536)

- Restore Variant M review detail: direct current-or-specific-proposed value
  controls record the existing exact-field decision without generic outcome
  controls or a submit step. Readable reviewer facts and secondary,
  retryable evidence/history remain available without blocking the decision.
  When a decision response cannot be reconciled against authoritative detail,
  every field remains blocked instead of offering stale choices or claiming
  that the case was refreshed. A later successful authoritative detail read
  made after the lock restores eligible choices.
  (#531)

- Restore the Variant M editorial shell and complete attention-first Library.
  Library rows now lead with readable work identity and truthful field counts,
  while Attention groups only human-beneficial fields with bounded reason and
  outcome context, complete per-work counts, and exact-field links. Complete
  Library discovery includes current work before or without review admission,
  and bounded search covers readable author and media identity. Persistent
  routing, 44px shell controls, bounded search,
  44px Library lenses, title links, preview and pagination actions, readable
  bounded long-title wrapping, compact single-surface flow, light/dark themes,
  and hostile-text safety remain intact. (#535)

- Reconcile the exact 0.14.0 application contract and finite deflation
  inventory, remove duplicated retired-asset assertions, and record that the
  separate 0.14.1 interface-fidelity milestone remains the smallest follow-on
  before a first-release decision. (#497, #498)

- Activate Library, Attention, review detail, Policy, and Activity in one
  persistent authenticated React shell at their existing URLs. Direct entry,
  progressive current reads, cancellation, focus and URL ownership, CSRF and
  build-identity mutation gates, and authoritative recovery replace the
  authenticated embedded frontend without expanding product authority. Scan and
  retry acknowledgements, preparation-panel focus, duplicate activity events,
  uncertain Activity commands, and persistent chunk recovery now preserve
  their respective current-state contracts. (#496)

- Establish an exactly pinned React, strict-TypeScript, Vite, TanStack Router,
  and TanStack Query browser platform without switching current routes. Exact
  OpenAPI types, the handwritten HTTP boundary, hashed manifest-bound assets,
  stable recovery, layered frontend tests, and wheel/container exclusion of
  Node and source artifacts prepare the separate authenticated cutover. Series
  values now retain their sequence in review display, and changing a Library
  search clears stale preview and cursor state. (#495)

- Compose authenticated browser workflows behind one application boundary with
  bounded case, Library, Attention, and preparation views. FastAPI now receives
  named operations instead of a broad repository, while SQLite remains the sole
  transaction owner and all existing security, currentness, idempotency, and
  API contracts remain unchanged. Unconfigured scan, retry, pause, and resume
  controls preserve the typed `unavailable` response instead of reporting a
  persistence or library failure. (#494)

- Remove the retired qualification-only shadow-review product workflow,
  request/attempt/dispatch/result persistence, browser model-advice projection,
  and its qualification machinery. Preserve the deterministic packet and
  automatic-decision contracts, and retain one source-only contained-reviewer
  workbench kernel for sanitized bounded experiments. Contained profiles now
  require the output allowance to fit the child file limit, root schemas to
  describe objects, and the bound sandbox version to match before use; reasoning
  overrides are serialized as one TOML-safe value. (#493)

- Contract the installed command surface to the authenticated `review-serve`
  launcher. Catalogue, report, batch, proposal, case, deterministic decision,
  and contained-reviewer experiments now live in a typed source-only contributor
  workbench with disposable state, production-target refusal, isolated-network
  attempt evidence, exact admitted authority snapshots, owner-private persistent
  state, exact catalogue inspection, bounded redacted results, and
  wheel/container exclusion. The runtime launcher preserves typed configuration
  failure reporting.
  (#492)

- Label persisted qualified-automatic Activity events accurately, leave
  unrecognised origins neutral, and keep eligible prepared libraries available
  for authenticated, CSRF-bound later consent after another library is already
  enabled. Library revocation remains scoped to that library. (#507, #508)

- Let Activity consent proceed for the currently eligible fields when another
  field is unavailable or lacks permission, while connection outages still block
  consent. Capability-disabled fields no longer inflate Library or Attention
  counts, and successful later observations restore their eligibility. (#505, #506)

- Persist denied field readiness across restart, preserve unexpected-behaviour
  suspensions during read-only probes, disclose and bind the exact destination
  subset for initial and incremental Activity consent, and restore clear
  qualified-automatic versus neutral event-origin labels. (#505, #506, #507,
  #508, #510)

- Keep a returned automatic field on its durable manual hold until the requested
  exact-current scan completes successfully, and bind release to the originating
  human decision so a concurrent newer hold cannot be cleared. The accepted
  return request is persisted before success, forces a successor scan when
  discovery is already active, resumes after restart, and is recovered through
  bounded rotating pages without starving later requests. Continuous
  field-effect shutdown now uses the configured grace deadline and fails before
  closing the shared repository while retaining a live timed-out worker. (#503,
  #504)

- Bind the exact-container checkpoint probe to current persisted synthetic
  consent, and correct the deployment contract for the composed guarded
  Audiobookshelf target and its write gates. (#509, #510)

- Add continuous guarded application for explicitly consented Audiobookshelf
  libraries. Current human and sealed qualified automatic decisions share the
  same 14-destination kernel, exact-field value guards, one fair instance-wide
  mutation slot, pause and library revocation, durable manual holds, policy
  demotion, scoped capability suspension, restart recovery, and a quiet Activity
  surface. Consent is exact-policy-bound, write capability is proven before
  enablement, blocked work is paged fairly, and restart recovery drains every
  bounded page. Audiobookshelf still supplies no CAS, so the documented same-field
  lost-update window remains; ambiguous requests are reconciled forward rather
  than blindly retried. Continuous operation is product-owned and does not wait
  for an upstream CAS feature. (#153)

- Add the supported Audiobookshelf guarded-write canary: one API-key
  destination-minimal metadata/tags or digest-verified cover request follows a
  final authoritative pre-read and is followed by read-back/reconciliation. ABS
  supplies no CAS, so the documented same-field lost-update window remains.
  Authentication/basic-access failures durably suspend the connection, while
  permission, field incompatibility, and unexpected effect behavior suspend only
  the affected destination; claims enforce those scopes transactionally and the
  canary receipt binds the validated append-only event sequence. No
  continuous activation is delivered separately by #153; bulk, rollback,
  embedded-media, and production qualification remain unavailable. (#150)

- Add the supported single-container operational substrate: absent or
  exact-current startup, readiness/liveness, graceful claim draining, typed
  scoped incidents, opt-in bounded stage timings, a bounded private cover
  recovery adapter, and previewable re-redacted support bundles. The container
  remains non-root with one volume, one HTTPS port and read-only media; no
  migration, notification, metrics platform, queue console, release or
  production-service access is added. (#151)

- Add the origin-neutral guarded field-effect kernel for immutable owner and
  sealed qualified automatic decisions. All 14 ebook destinations use one
  deterministic field plan (with atomic series), last-feasible preclaim guard
  refresh, repository-atomic authority/consent/pause claim, and target-side
  optimistic concurrency; persisted targeted-read/forward-reevaluation actions;
  a bounded restart runner for those actions and cleanup; transaction-local
  current-lineage and automatic-safety checks; bounded retries, independent
  sibling outcomes, and retryable atomic-file
  private cover cleanup. Existing databases validate every canonical effect
  envelope, exact scalar type, and matching row before opening. The only writable
  target is a deterministic fake; no Audiobookshelf, media, CLI, bulk, release,
  or production write capability is added. (#149)

- Qualified metadata-policy defaults now create immutable, decision-only
  automatic dispositions for the sealed F19a envelope. Preview, owner pause,
  durable safety suspension, and explicit recovery keep unresolved or
  suspended fields in human Attention. This adds no apply, target, media, or
  production-service write capability.

- Add an explicit metadata-policy Preview posture that records immutable,
  non-authoritative F19 outcomes as newly eligible exact-current cases pass the
  existing background projection stage. Outcomes bind exact policy, rule,
  catalogue, evidence, candidate, and qualification identities; replay is
  idempotent, stale work publishes nothing and safely reuses its current
  pipeline row, and authenticated bounded history/sample views remain separate
  from human decisions and model advice. Entering Preview leaves Library and
  Attention work unchanged. Automatic intent can be validated, drafted,
  previewed, and activated only while the stored posture is Preview; proposal
  admission treats only those Automatic categories as Ask, preserving existing
  Ask, Ignore, and Unsupported human behavior. Qualification remains empty and
  every observed result remains non-authoritative. Leaving is impact-only and cannot be confirmed;
  no automatic decision, apply, target, media, artifact, or write authority is
  added. (#478)

- Activate an exact reviewed metadata-policy draft with one atomic generation
  cutover. Current old-policy cases retire before successors progressively
  return through the existing review pipeline; Ask remains manual, Ignore is
  non-actionable, and unqualified Automatic remains unavailable. Durable
  fixed-cohort progress, typed failure/retry, and dismissible completion add no
  automatic decision, apply, target, or media-write authority. Exact
  generation-one library overrides remain replayable, and a newly configured
  library stays proposal-ineligible until the next explicit activation binds
  its resolution. (#477, #343)

- Add closed, canonical metadata-policy draft and sparse per-library preview
  contracts. The built-in policy stays active; preview creates no activation,
  queue authority, decision, apply, or target/media write capability.

- Activate bounded continuous review reconciliation in the supported container:
  first-start and interval scans coalesce through the persisted two-lane
  pipeline, fair claims and provider circuit state survive restart, and the
  protected M shell gains one compact activity popover with pause, resume, and
  eligible retry controls, including per-library scan. The path remains
  decision-only and write-free. Material-source reuse prefers stable media
  revisions and conservatively falls back to the item revision for unversioned
  EPUB or cover bytes while retaining absent, null, empty, malformed, and value
  states for reviewed metadata and the root cover field. Initial
  catalogue preparation is isolated per library, and compact activity reads one
  aggregate repository snapshot; one supervisor lane schedules from that same
  bounded snapshot and a retained deadline. Superseded Library refresh responses
  cannot replace newer exact-current results. Fair claims select their bounded
  library before using aged/recent indexes, and a durable stage transition may
  atomically persist proposal/admission output, transition its stage, and claim
  the next fair action before that action executes, reducing full-synchronous
  commits without weakening restart recovery. Pipeline-composed
  services borrow the already-validated stage repository without taking ownership;
  provider disposition is checkpointed with report publication, and admission
  without a review case completes without a no-op projection stage. The report
  checkpoint may claim the next fair action in the same durable transaction.
  Required exact-wheel qualification continuously proves the installed restart
  workflow over a representative 45-item, three-library cohort; the complete
  1,000-item workload and memory ceiling remain an explicit exact-head slow tier,
  measured across its real report, proposal, and admission processing path.
  Fixed, path-free evidence distinguishes subprocess timeout from a genuine
  nonzero test failure through the shared qualification tiers, and a stalled
  test emits one bounded path-free identifier after 30 seconds without exposing
  raw test output.
  Transient lane and final-attempt `Retry-After` values
  extend only the transient circuit; paused retry and disappearing activity
  retain truthful controls and visible keyboard focus. Stable terminal failures
  wait without repeated repository validation until an owner action, while the
  scheduling lane rechecks automatic deadlines between bounded work batches so
  a large backlog cannot starve another library's interval scan. (#475, #473)
- Tier public qualification into independent required fast-source,
  exact-installed, and protected-browser jobs while preserving one stable
  aggregate gate. Scale, endurance, exhaustive, and resource assertions retain
  an explicit exact-wheel slow owner; the first moved resource proof is the
  unchanged maximum-shape metadata-report round trip. Each tier has a documented
  command and path-free cohort manifest, the complete exact-head command remains
  reproducible, and bounded evidence now distinguishes provenance, setup/build,
  nonzero test, and subprocess-timeout failures. No product or write authority
  changes. (#482)

- Add owner-requested progressive library preparation to the authenticated
  review surface. Exact-current catalogue, report, ProposalSet, and ReviewCase
  work is persisted, duplicate-safe, restart-safe, and independently advanced
  across two fixed lanes. Complete scans also retire present works that become
  ineligible, and later eligibility permits readmission. Catalogue discovery,
  shared provider pacing, and proposal-currentness drift remain bounded and
  race-safe; slow currentness checks do not hold the cross-lane writer lock and
  a second file appearing during extraction publishes no report or case. The
  workflow confirms staged retirements with a final discovery, prevents terminal
  scans from claiming stale children, refreshes changed Library projections,
  and treats admission-time disappearance as stale. It stays decision-only and
  write-free. (#474)

- Restore the authenticated review frontend to the protected variant M visual
  contract across Library, Attention, and exact-current Detail. The branded
  editorial shell, light/dark palettes, work identity and status language,
  attention-only field navigation, current-to-outcome controls, progressive
  evidence/history, feedback, and compact layouts now qualify at 1440, 884,
  390, and 320 CSS-pixel widths. Theme selection is a presentation-only secure
  cookie; it adds no automatic decision, target-write, bulk, reversal, or
  library-wide history authority. (#455)

- Reconcile immutable decision retries with authoritative state. A committed
  same-key/same-payload request now replays its original acknowledgement even
  after case supersession, while changed payloads retain an explicit conflict.
  The browser reloads detail/history after a current-case conflict, routes a
  stale case back to the authoritative Library, and uses the same bounded
  request identity once after response loss before reloading or requiring sign
  in, without claiming that an ambiguous decision was unrecorded or adding
  client-side authority.
  (#456)

- Preserve the selected Library lens when searching and keep Attention search
  on its own route. Title, library, and item matching now applies one
  locale-independent Unicode casefold contract on both sides of the bounded
  SQLite query, while escaped wildcard behavior remains literal. Cursor
  encoding rejects an out-of-range next offset before an unusable link can be
  emitted. (#461)

- Make every evidence reference in an authenticated exact-current case
  reachable through deterministic, case-bound pages of at most 50 items.
  Cover `source_id` references and provider/candidate identity now survive the
  hostile-safe projection, while the browser progressively loads the bounded
  ledger without provider access or new decision/write authority. (#458)

- Read each authenticated exact-current case, Library page, and Attention page
  from one authoritative SQLite snapshot. Concurrent supersession or human
  decision recording can no longer mix stale currentness, evidence, history,
  decision summaries, rows, or counts within one response; snapshot failures
  remain bounded unavailable responses without partial projections. (#457)

- Add one supported canonical-HTTPS path for `review-serve`. Explicit
  certificate and private-key configuration is validated as a matching,
  owner-controlled descriptor-bound identity with an owner-only key before
  database or listener startup;
  immutable anonymous snapshots, AEAD TLS policy, and bounded connection/header
  handling prevent post-validation identity drift and unbounded edge reads.
  Explicit unencrypted PEM-envelope classification rejects even
  empty-passphrase encrypted keys before SSL parsing.
  The server has no plaintext escape hatch and retains the existing canonical
  Host/Origin, secure-cookie, HSTS, and proxy-distrust boundary. (#459)

- Align the authenticated review API contract with runtime behavior. Protected
  operations now declare the owner API-key cookie and required mutation
  headers; absent decision cases return not-found, and logout distinguishes
  invalid sessions, CSRF mismatch, and revocation outages without clearing a
  still-active cookie. The deterministic generated reference carries the same
  private, value-free contract. (#460)

- Project exact-current model advice from only the latest persisted
  reevaluation attempt. A newer pending attempt is no longer hidden by an
  older failure, and a newer failure cannot resurface stale successful advice;
  the projection remains read-only and non-authoritative across restart and
  reopen. (#462)

- Activate the complete F16a browser round trip. The authenticated owner can
  choose `accept`, `keep_current`, or `reject` directly from an exact unresolved
  field, receives only a server-acknowledged success message, and advances to
  the next unresolved field. Failures preserve the choice and focus a retry;
  automatic-only cases remain read-only. A fresh per-page CSRF token stays in
  memory only, and pinned Playwright 1.61.0 Chromium qualifies desktop and
  390-pixel layouts, keyboard/focus, hostile text, restrictive CSP, private
  storage/cache, bounded assets/DOM, failure retry, restart, and a fresh browser
  context without adding automatic, apply, target-write, bulk, or reversal
  authority. (#450, #152)

- Add the third F16a production increment: an authenticated client can record
  one immutable `accept`, `keep_current`, or `reject` decision for one
  exact-current ordinary field. The command is proposal-, target-, actor-,
  CSRF-, and idempotency-bound; SQLite publishes the decision and current-case
  audit event atomically, replays the original acknowledgement, and rejects
  stale, conflicting, unsupported, or already-decided attempts. Decisions
  survive restart and exact-current reopen and remove only their exact field
  from attention, without automatic, apply, target-write, bulk, or reversal
  authority. (#449)

- Add the second F16a production increment: authenticated bounded library
  lenses and a separate exact-field attention queue. The default view places
  genuine unresolved human-attention work first while deterministic
  automatic-only cases remain searchable and inspectable without controls.
  Search-bound cursors, exact-current joins, 50-item response limits, and
  text-only DOM rendering add no decision, apply, or target-write authority.
  (#448)

- Add the first F16a production increment: an authenticated read-only
  `review-serve` boundary for one exact-current ReviewCase, bounded evidence,
  current-case admission history, and deterministic authenticated OpenAPI.
  Opaque SQLite-backed owner sessions, Argon2id hash-file verification,
  throttling, Host/Origin/CSRF/proxy checks, private caching, and restrictive
  browser headers add no human-decision, automatic, apply, or target-write
  authority. (#447)

- Add `shadow-decision-preview` for one exact current ReviewCase. The command
  opens an existing report database read-only and no-create, compiles the exact
  admissible action catalogue, emits one canonical deterministic decision per
  proposal, and safely selects manual on ambiguity, conflict, unsupported
  state, or incomplete combination closure. Policy v5 admits unique
  selected-EPUB source-supported report-only actions for authors, language,
  publisher, publication date, and publication year while retaining stricter
  title eligibility. The preview performs no provider/model call, persists no
  decision, and adds no default, approval, apply, or write authority. Accept
  Audiobookshelf's item-relative EPUB path form only when its absolute service
  path proves the same final library-relative file. (#384)

- Accept bounded duplicate-free reviewer JSON with insignificant formatting
  variance at the transient provider boundary, while retaining exact raw bytes
  and canonicalizing before typed domain construction and persistence. Partition
  finite recommendation schemas by one shared allowed bibliographic combination
  so incompatible nonmanual actions cannot satisfy the same schema branch;
  every compiled action remains reachable and trusted validation remains
  authoritative. Advance the clean exact-current request contract to
  `shadow-review-request-v9`; request-v8 stores require a clean rebuild. (#440)

- Make the qualification shadow-review structured-output schema acceptable to
  the provider by expressing the same exact compiled action variants with the
  supported nested `anyOf` union and rejecting `oneOf` locally. Domain
  validation still requires one exact compiled action and its complete
  citation closure. Recognize the provider's bounded nested invalid-schema
  error envelope as `response_format` while malformed, mixed, or extended
  signals remain generic runtime failures. Advance the clean exact-current
  request contract to `shadow-review-request-v8`; earlier request-v7 stores
  require a clean rebuild. (#438)

- Bind the final qualification shadow-recommendation contract. Manual advice
  may now retain an empty required `problems` list when it has a bounded reason
  and exact citations but no adverse fact; present problems remain bounded
  actual adverse facts. The prompt now requires `candidate_id` only for exact
  packet-candidate advice, makes keep-current an evidence-positive conclusion,
  and sends insufficient, conflicting, unrepresentable, or out-of-catalogue
  support to manual review without output repair. Advance the exact tuple to
  `shadow-review-prompt-v3`, `codex-shadow-profile-v2`,
  `shadow-review-result-v2`, and `shadow-review-request-v7`; packet v5,
  compiler policy, provider transport, and qualification-only authority remain
  unchanged. Earlier pre-release request-v6/result-v1/profile-v1 state requires
  a clean rebuild. (#419, #420, #421)

- Compile a deterministic, packet-only catalogue of policy-admissible
  `recommend_candidate`, `recommend_keep_current`, and `manual` shadow-review
  actions before provider dispatch. Each action has a stable identity and an
  exact citation and bibliographic-combination closure; rejected nonmanual
  actions retain stable typed exclusions and unknown obligations fail closed.
  The structured-output schema now enumerates only compiled actions, and
  trusted result validation requires an exact action and action-bound
  citations. Advance the exact-current request contract to
  `shadow-review-request-v6`; result v1, packet v5, prompt, model, effort,
  provider transport, and qualification-only authority remain unchanged.
  Earlier request-v5 stores are rejected without migration. (#431)

- Isolate ordinary qualification-reviewer provider state from primary
  credential material. Authentication management retains its explicit writable
  store, while ordinary status and inference mount a disposable per-attempt
  `CODEX_HOME` and nest only descriptor-bound `auth.json` read-only; version
  verification runs in a credential-free, network-isolated sandbox. Credential drift, retained
  attempt state, and durably marked cleanup failure stop later dispatch without
  restoring or exposing private bytes. Persist requested reviewer identity separately from
  provider-observed model, route, tier, effort, fallback, usage, and cost using
  explicit `observed`, `not_exposed`, or `unverifiable` states. Advance offline
  inspection to `shadow-review-inspect-v3`; earlier pre-release databases are
  rejected without migration. (#433)

- Add bounded deterministic decision summaries to every shadow-review proposal
  and candidate while retaining the complete raw citation projection.
  Candidate summaries count distinct admitted local and provider correlation
  groups, distinguish target-only preservation, state work/edition/cover
  scope and conservative selected-manifestation binding, expose complete
  author-role inclusion and same-representation non-author exclusions, bind
  dependent bibliographic candidates to their allowed combination IDs, and
  describe publication event, precision, paired-destination consistency, and
  the runtime's report-only/unsupported write state. Proposal summaries state
  only whether the closed policy found sufficient candidate evidence,
  evidence-backed preservation, or a manual outcome; they do not choose a
  candidate or grant authority. Populated current values without an
  evidence-backed preservation candidate remain explicitly target-only even
  alongside competing candidate/manual evidence, and admitted provider-cover
  provenance counts as independent provider support. Bound complete
  creator-role facts by the maximum admitted aggregate correlations rather
  than two representations.
  Advance the exact-current reviewer contracts to `shadow-review-packet-v5`
  and `shadow-review-request-v5`, persist the exact request schema
  discriminator, and reject earlier pre-release shadow-review stores for a
  clean rebuild. (#423)

- Bind dependent title, subtitle, and series advice to explicit
  `bibliographic-relationships-v1` combinations without rewriting source
  strings or changing per-destination proposal identities. Every combination
  selects an already-admitted candidate or preserves the exact target-field
  state; an admitted alternative that already preserves the target is encoded
  as that target-bound preserve member, not as a candidate change. Ambiguous
  same-scope and disagreeing edition/work titles remain report
  provenance; exact component containment can veto duplication but cannot
  split or create a title. When no safe tuple exists, affected results remain
  unresolved where they retain candidates; exact target-bound companions are
  projected as manual without inventing alternatives. Review cases and packet
  v4 project the exact relationship contract; unchanged result-v1
  recommendations must derive exactly one allowed tuple. Advance clean
  exact-current contracts
  to `proposal-set-v3`, `fixed-conservative-v6`, `review-case-v2`, and
  `shadow-review-packet-v4`; prior proposal stores require a clean rebuild.
  (#425)

- Evaluate language and publisher candidates through a closed field policy
  without collapsing their original values or provenance. Canonical ISO
  639-1/639-2 aliases and non-contradictory canonical script/region detail are
  acceptable language outcomes; deprecated `bh` remains an alias of favored
  `bih`. Different base languages, incompatible specified script/region, and
  malformed codes remain unresolved. Valid edition-bound publisher and imprint
  statements with an accepted provider manifestation anchor may be acceptable
  at any publishing-business level without asserting corporate equality or
  hierarchy.
  Add the non-selecting `acceptable_choice` proposal outcome and allow
  evidence-supported language/publisher preservation to retain every
  acceptable raw alternative. Work-scoped, malformed, unrelated, and
  target-only statements gain no authority. Reviewer packets replay every
  retained candidate and citation. Advance the exact-current proposal policy
  to `fixed-conservative-v5`; earlier proposal-store policy shapes are rejected
  without migration. (#424)

- Project publication-date evidence to its strongest independently supported
  precision. Exact agreeing days remain full-day `publishedDate` candidates;
  differing days in one month or a day plus an independently supported month
  produce one `YYYY-MM` candidate with the union of their references and
  correlations; different months retain only shared `publishedYear` support;
  and different years remain conflicting. Original observations and finer
  conflicts remain cited rather than being padded or discarded. Reviewer
  packets preserve the resulting `manifestation_publication` event and
  `month` or `day` precision. The Audiobookshelf runtime remains report-only:
  partial-date writes are unsupported and no proposal grants write authority.
  Advance the exact-current proposal policy to `fixed-conservative-v4`; earlier
  proposal-store policy shapes are rejected without migration. (#422)

- Preserve publication-date precision and incompatibility explicitly under
  `closed-title-creator-publication-v4`. Date links distinguish exact,
  month-compatible, year-compatible, finer-conflict, event-incompatible, and
  manifestation-incompatible observations while separately retaining shared
  year/month/day precision. Provider dates can vote only through an exact
  checksum-valid local/provider ISBN-family binding with ebook or unknown
  applicability and no closed format or work contradiction; Google Books
  `publishedDate` text is promoted only through that same gate. Work-first and
  unsafe manifestation evidence remains reviewer-visible but non-voting.
  WORK-lane qualification preserves only bounded unresolved edition
  publication text from edition authority so report finalization can retain
  its manifestation-incompatible explanation. Naive timestamps remain visible
  non-voting evidence and only timestamps with `Z` or an exact signed
  `HH:MM` offset in the source lexical can be promoted; offset-second and
  fractional-offset-second forms remain visible but non-voting, even when
  normalization removes zero offset seconds, and are skipped before
  provider-date comparison. Valid local publication and typed date-event facts
  bind the observed lexical to its exact parsed canonical value and precision,
  rejecting malformed or mismatched isolated-inspector IPC and report replay
  before proposal evaluation. Non-valid local date facts preserve the observed
  lexical and typed event but cannot retain a canonical value or precision
  across IPC or report replay.
  Reviewer packets
  cite every non-null local operand named by a retained date link, require
  complete proposal evidence-reference coverage, and require every ordinary
  replay to reproduce the exact proposal, case, citation, and packet projection
  of the immutable source report. Preserved identities cannot hide rewritten
  destinations, date typing, evidence references, or citations.
  `publishedYear` uses shared year support, while full-day `publishedDate`
  always requires two independent sources.
  Contradictory or multiple provider work relations retain one canonical
  candidate binding plus one closed contradiction fact, suppress unrelated
  work-scoped authority, and keep the edition date visible but non-voting.
  Timestamp comparison uses the stated lexical calendar day rather than UTC
  instant equality. Advance exact-current contracts to
  `metadata-report-v22`, `fixed-conservative-v3`,
  `shadow-review-packet-v3`, and `shadow-review-request-v4`; earlier
  pre-release schemas are rejected without migration under ADR-0013. (#409)

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
