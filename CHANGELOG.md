# Changelog

Mechanical Librarian has not been released. The numbered sections below are
internal checkpoints, each matching a GitHub milestone. None is tagged or
published, and the package version stays `0.0.0`. Versioned release sections
start at the first release, 1.0.0 (#1349, #94).

An entry marked **Clean rebuild required** changes persisted state, so an
existing `/data` volume must be rebuilt; [upgrades](docs/operations/upgrades.md)
says how. Sections marked "in progress" are still open. The numbers in
brackets are GitHub issues.

## 0.31.0 — Coverage: admission rules and reviewer-informed covers (in progress)

- **Clean rebuild required.** A source lookup that failed inside Mechanical
  Librarian reads as **Failed in Mechanical Librarian** in Sources and, when
  nothing matched, in Library's Not matched lens, instead of "Could not be
  reached" or "Providers unavailable", and it is not retried. Inventaire no
  longer fails that way when it finds more than one work (#1498).
- **Clean rebuild required.** An ebook whose title carries its series name in
  brackets, such as "Title (Series Name)", now matches the work its providers
  call "Title" by the same authors, unless the brackets name a volume, part,
  collection, trilogy, guide or workbook, or end in a number. When an ebook's
  lookup at Open Library, Google Books, Inventaire or iTunes finds nothing, its
  report records which searches were sent. An ebook whose providers found only
  other works now reads "Found, but not this work" in Library's Not matched
  lens instead of "No provider matched", and its report says so for that
  source (#1499).
- An ebook is looked up only in Google Books, Hardcover, Inventaire, Open
  Library and iTunes Search. Audible, Audnexus and AudioSilo get no ebook
  request or retry and no longer appear in an ebook's lookups (#1486).
- **Clean rebuild required.** A title that only drops a leading "The", "A" or
  "An", a bracketed ISBN suffix on a title-matched work, and a packaged "A
  Novel" or "Stories" subtitle no longer reach you. A language spelled `en`,
  `eng` or, on an ebook, `en-GB` is respelled to its name, such as "English",
  automatically (#1496).

## 0.30.0 — Audiobook matching and editions (in progress)

- **Clean rebuild required.** Hardcover's audio editions are read for every
  matched audiobook work and compared with the file. The report records when
  an edition with a different narrator matches the file and the Audible match
  does not. A Hardcover edition never counts as a second source (#962).

- **Clean rebuild required.** A placeholder or cut-off audiobook synopsis is
  filled under the default option (a cut-off one only by its own full text),
  and an "About the book" header line no longer counts as a difference.
  Marketing lines are never chosen as subtitles, and genres or synopses from a
  store in another language wait for your choice (#1317).
- **Clean rebuild required.** Every language is written as one display name,
  such as "English"; a current `en` or `eng` is offered for review, never
  rewritten. The `preserve` language option is gone (#1314).
- **Clean rebuild required.** An Audiobookshelf item holding audio and an EPUB
  is prepared as an audiobook, with the EPUB's details kept only as supporting
  evidence. The audio filter lists only items that hold audio (#1319).
- **Clean rebuild required.** A library can declare its languages
  (`languages`); Audible searches those stores first, and an edition whose
  language or cut contradicts the recording is not matched (#1315).
- **Clean rebuild required.** An audiobook with no admitted recording is
  searched again weekly; its open review case changes only when the new search
  admits a recording (#1451).
- **Clean rebuild required.** An audiobook whose Audible title differs in shape
  (joined subtitle, leading number, series-position title) matches when runtime
  and narrator or series number agree and nothing contradicts it (#1307).
- **Clean rebuild required.** A matched audiobook edition is looked up directly
  on the next preparation, so it is not lost to a sibling edition; one
  recording's regional ASINs offer one choice (#1316).
- **Clean rebuild required.** AudioSilo answers listing many regions keep the
  queried ASIN's entry instead of failing, and an AudioSilo lookup miss is no
  longer a failed request (#1308).
- Setup lists iTunes with the book and ebook providers and suggests it only for
  libraries with ebooks (#1458).
- Settings edits a store-managed library's languages: add by name, reorder,
  remove, first preferred. A file-managed deployment shows its list (#1466).
- At 200% text, Settings' language picker shows its whole label and the save
  tray scrolls with the page once taller than a third of the window; setup's
  step list, the masthead's background-work popover and Activity's pager
  controls stay in view and full size (#1491, #1503).
- When setup's step list is too narrow to hold a step's name beside its Change
  button, as it is beside the steps with 200% text, the button moves under the
  name, so words such as "Audiobookshelf" stay whole (#1508).

## 0.29.3 — Review fixes and hygiene (in progress)

- The masthead names only the Library link as the current page on Library,
  a review and Attention; the Mechanical Librarian brand link no longer
  claims it too (#1488).
- Choosing an item from the Attention queue keeps the chosen work's header,
  which takes focus, fully on screen and clear of the masthead at enlarged
  text (#1487).
- Each Attention queue row's name for screen readers ends with its work's
  title, so the same field in two works is no longer announced identically
  (#1361).
- Codex sign-in from Settings or setup shows the link and one-time code the
  bundled Codex CLI prints, instead of failing at once. The link opens in a
  new tab, and the code stays still while you select it and has a Copy
  button (#1493).
- When the bundled Codex CLI gives up on device sign-in itself, Settings and
  setup say the sign-in expired and offer to start again, instead of reporting
  a permission or process failure (#1529).
- Retry now no longer parks a provider scope that has no outstanding lookups,
  so lookups that fail there later are retried and Activity stops asking for a
  correction nothing needs (#1375).
- Resuming automatic handling, or lifting a title suspension, now also gives an
  automatic title decision to a work re-read while handling was paused; an
  owner's hold carried to it shows as held (#1364).
- Opening Mechanical Librarian's address while signed in goes to Library, and
  a link opened while signed out returns to its page after sign-in. Sign-in
  returns only to a page of this workspace (#1371).
- Activity's header and summary say how many stopped changes wait on you,
  such as a description to restore in Audiobookshelf, instead of reading "No
  action required" (#1430).
- The Works panel's lenses sit in one row that scrolls sideways, the pager
  offers only the moves that exist (none for a single page), and search is a
  rounded field with a × inside to clear it (#1520).
- Library names one current page: the current lens is marked current within
  it. Choosing an Attention item on a phone keeps Back to works in view, and at
  enlarged text Attention's work titles no longer break mid-word (#1530).
- A work's collapsed list of fields with nothing to decide is headed "No
  change:" and their names, counts waiting or unavailable fields separately,
  and says no source offered a different value (#1514).
- A cover write held up because Audiobookshelf could not be reached now waits
  and is applied once the connection is back, up to three times, like every
  other field, instead of stopping. No clean rebuild is needed (#1415).
- A field decision shows the question, one line with the field's own reason,
  and the values: a proposed value names its sources, and generic lines,
  empty sections, idle library cards and "No recommendation retained" are
  gone. Reminders that writes are protected remain only in the guided first
  review and Activity's permission control (#1519).
- **Clean rebuild required.** A work that returns to an earlier content, for
  example after you choose the previous value again, reads as new: it no
  longer shows its earlier decision as written while the library holds
  another value. Return to automatic also stops a field showing as held
  (#1358).
- A work whose preparation could not finish is no longer counted under "No
  attention needed". Library gives such works their own Could not finish lens,
  lists them after the works needing a decision, and names their count beside
  Works. No clean rebuild is needed (#1413).
- An optional description or genre choice says once why no review is needed
  (the proposed values only shorten or extend the current one), instead of
  also showing a note that the sources disagree or that a change needs you
  (#1450).
- Activity's advice for finding a stopped change also says to clear filters
  when a search or event type is active. A write that closed because the item
  changed reads "Item changed; closed safely", and other closed writes read
  "Closed safely", instead of "Stale" (#1542).

## 0.29.2 — Delivery reliability and hygiene

- **Clean rebuild required.** Automatic authority binds only the operations
  each qualification permits, so ordinary source changes no longer rotate it;
  decisions made under an earlier qualification are refused (#1396).
- Settings accepts an OpenRouter description model with no listed request fee,
  and automatic descriptions no longer apply placeholder, praise-heavy, cut-off
  or marked-up rewrites. No clean rebuild is needed (#1400).
- OpenRouter checks work however large the model list grows; timeouts,
  oversized answers, data-policy refusals and too many images each get their
  own reason and remedy in Settings and setup (#1330, #1331, #1332).
- Setup describes iTunes as a check of an ebook's title and author against
  iTunes audiobook listings. An audiobook inspection with no recording facts
  fails that item instead of being planned without them (#1303).
- A later provider throttle can lengthen a retry wait but not shorten it, so a
  pacing pause no longer uses up a recovery attempt (#1318).
- A review case's history shows decisions and description choices in the same
  order on every read (#1209).
- The activity projector's events now reach the service log as JSON lines
  (#1304).
- Renovate moves Node, Python and uv pins together in one pull request; a new
  Node major or Python minor version waits for approval (#1177, #1448).
- The standalone Attention page is gone; Attention lives in Library. An old
  `/attention` bookmark opens Library with Attention open (#700).
- The Audiobookshelf field-effect canary proves title, authors and language
  through real automatic decisions again, and its catalogue fake answers an
  operator book that carries its own ISBNs as a real catalogue would
  (#1427, #1545).
- Activity's page number field keeps focus when a filter change finishes after
  you have moved into it (#1554).

## 0.29.1 — Clarity and accessibility

- At 200% text the write-permission panel, Policy's preview actions and the
  Review complete card stay on screen; that card's heading comes into view
  clear of the masthead, and Activity keeps the focused pager control in view.
  Activity names languages, setup's dark mark meets contrast, and Activity
  chips, Details and setup's Change buttons are full targets (#1490, #1491).
- The masthead holds still as its status changes and scrolls away once taller
  than a quarter of the window. Library opens with its write handoff in place
  and stays current on reviews and Attention; saving settings moves nothing.
  At 200% text Activity, Policy, Settings and setup refusals stay in view, and
  words break only where they must (#1291).
- Review choices form one keyboard-navigable radio group, field tabs state
  their status in words, and a cover records in one tap with its pixel size and
  sources (#1290).
- Text meets WCAG AA contrast in both themes, controls are 44px targets, and
  focus stays put or returns sensibly after commands, failures and drawers
  (#1288).
- Setup and sign-in failures name what went wrong, the address and one remedy;
  setup suggests providers per library and confirms each step (#1287).
- The workspace opens in your saved or system theme without a light flash, uses
  the self-hosted Inter typeface and one style for main actions (#1289).
- Labels, states and reasons read in plain words: running times in hours and
  minutes, languages by name, sizes in MB and one date format (#1286).

## 0.29.0 — Owner trust and control

- Claiming a new instance needs the setup claim token the container prints in
  its log, so a network visitor cannot take ownership. Clean rebuild required
  for incomplete setup only (#1383).
- Wrong passwords slow only the client sending them, and new owner passwords
  need at least 12 characters. Clean rebuild required for the session database
  only, which signs every browser out once (#1384).
- A write Audiobookshelf could not be reached for is retried once the
  connection is back, without sending twice, and Activity says why a change
  waits or stopped (#1402).
- A write in progress completes when the system clock steps backwards, and a
  store holding rows outside the schema's rules is refused when opened (#1386).
- **Clean rebuild required.** You can enter a title, subtitle or publisher,
  choose genres to keep, change or withdraw a decision, and choose a replaced
  value again as Previous value (#1285).
- **Clean rebuild required.** Works no provider matched have their own Library
  lens, Not matched, with one reason each, and no longer count as No attention
  needed (#1407).
- **Clean rebuild required.** Descriptions Audiobookshelf stores escaped are
  recorded as verified and are no longer proposed again as their own text
  (#1424).
- **Clean rebuild required.** Accent-dropping or credit-prefixed contributors,
  promotional titles and praise-heavy descriptions are never written
  automatically; each stays your choice (#1283).
- **Clean rebuild required.** Activating a policy keeps each library's write
  permission, and a lapsed permission is shown with its reason (#1370).
- A policy activation with failures no longer blocks the next one, retry and
  Dismiss reach a clean end, and the impact preview marks rules it did not
  evaluate as "Not previewed" (#1369).
- Library counts come from one library-wide summary and name their unit, and
  the masthead and run summary describe preparation in the same words (#1284).
- Library and Settings show which sources limit or refuse lookups, how many
  wait and when they retry, with a link to add a key (#1282).
- Activity gathers a preparation's no-change checks into one "Checked N fields,
  no changes" row and loads again after a verified long write (#1281).
- The selected work stays on screen after edits, decisions and searches, and
  focus returns to the right place after retries and setup refusals (#1365).
- Reviewing write permissions keeps the checked libraries through refreshes,
  and an action after a session refresh succeeds on the first press (#1279).
- Checkpoint numbers state what each checkpoint delivers; the history is
  renumbered once (#1321).

## 0.28.0 — Audiobook discovery and admission

- Audiobooks are looked up only with Audible, Audnexus, AudioSilo, Open Library
  and Hardcover, and Hardcover and Open Library skip audiobooks known only by
  ASIN. Ebooks are unchanged (#1247).
- One slow or unavailable provider uses at most 20 seconds of an item's
  provider window, so later providers and cover downloads still run (#1248).
- **Clean rebuild required.** Audiobooks match an Audible recording despite
  series decorations or dotted-initial authors when runtime agrees within 90
  seconds; dramatisations and translations still do not match (#1260).
- **Clean rebuild required.** An audiobook Audible cannot find by title is
  searched by folder name, file title tag and shorter title forms, and a
  recording found that way is kept only when runtime matches (#1255).
- **Clean rebuild required.** A runtime-matched audiobook Audible refuses on
  title is looked up by ASIN on Audnexus and AudioSilo, and kept only when the
  answer names the same book (#1257).
- **Clean rebuild required.** Audiobooks enter the attention queue under the
  same conditions as ebooks; tag and ASIN fills alone no longer count (#1249).
- **Clean rebuild required.** Automatic audiobook metadata no longer drops a
  series from a title, replaces narrators with an unrelated cast or rewrites
  reordered authors (#1252).
- **Clean rebuild required.** An unresolved audiobook also offers its series,
  series position and a cleaned-up title from matched book evidence (#1253).
- **Clean rebuild required.** Descriptions with HTML closing tags are read
  instead of marked malformed, and credit lines, format words and placeholder
  languages no longer count as audiobook evidence (#1251).
- **Clean rebuild required.** Each audiobook report records why every provider
  lane ended and how far Audible's search went. The `schema-adopt` command is
  removed (#1258).
- Provider failures, throttles and recovery jobs write one value-free JSON line
  each to the service log (#1299).
- A startup failure reports the original error instead of an unrelated one
  (#1235).


## 0.27.1 — Application responsiveness

- **Clean rebuild required.** Library, search, Attention and work detail no
  longer slow as decisions accumulate, and repeat visits are faster (#1202).
- Activity keeps up with large batches of new history and answers sooner after
  a restart, while preparation runs alongside it (#1231).
- Opening Activity no longer makes preparation or owner writes fail with
  "database is locked", or stop preparation until a restart (#1225).
- Preparation slows far less while Library or Attention is open (#1224).
- Permission readiness and the pending-change list no longer slow as decision
  history grows (#1205).
- Library's write-ready notice appears with the first rows after a restart
  again (#1236).
- Library and Attention keep current rows in place, dimmed, while the next
  page, lens or search loads, and prefetch what the owner hovers or focuses
  (#1192).
- Reloads and return visits reuse the browser's copy of the application and of
  cover previews, and the application is served precompressed (#1193).

## 0.27.0 — First-run and review usability

- First-run setup lists what the deployment must supply, states password rules
  up front, and shows each refusal on its field with a cause and remedy
  (#1008).
- Settings keeps one draft across seven sections with a shared review-and-save
  tray; one theme rule and a cleaner phone layout span the workspace (#1034).
- **Clean rebuild required.** Setup and Settings recommend GPT-6 Luna for cover
  review, name the reviewer's model and show measured request costs (#1069).
- **Clean rebuild required.** Preparation reports per-run and per-stage
  progress, keeps failures apart from completed work, and survives restarts
  (#1012).
- **Clean rebuild required.** Owners can confirm a retained cover with a weaker
  match and revise it; acquired covers survive provider timeouts (#1078).
- **Clean rebuild required.** Empty ebook ISBNs, descriptions, series and
  genres, and audiobook descriptions and genres, fill from proved evidence
  (#1077).
- **Clean rebuild required.** Photo credits no longer replace authors,
  equivalent languages pose no false choices, and unchanged titles keep
  automation (#1083).
- **Clean rebuild required.** Policy preview and Library show refusal reasons,
  and owner actions take priority over background work (#1072).

## 0.26.0 — Audiobook decisions

- **Clean rebuild required.** Audiobooks with an admitted work identity get
  review cases for narrators, ASIN, series, description, language, title,
  authors (#966).
- Audiobook decisions apply through the guarded Audiobookshelf path; every
  library consent lapses on upgrade and Activity offers it again (#968).
- **Clean rebuild required.** Qualified audiobook Automatic choices cover
  title, authors, narrators, language, series and genres; writes are off by
  default (#967).
- **Clean rebuild required.** Audiobooks of several files are prepared as one
  item, and items with odd file names or no case complete normally (#965).
- **Clean rebuild required.** Complete chapter timing can support owner review
  when a provider omits the recording's running time (#1004).
- **Clean rebuild required.** An audiobook's media-health section states what
  each file's headers hold; a zero-byte file ends as unreadable media (#144).
- **Clean rebuild required.** Audible and iTunes covers get their own switch,
  and a provider cover is used only for an edition that provider observed
  (#957).
- Preparation and continuous application no longer stall on a recovery wake or
  on one no-change decision, and Activity stays available meanwhile (#993).

## 0.25.0 — Audiobook foundation

- **Clean rebuild required.** Single-file audiobooks yield local evidence such
  as ASIN, narrator, series, running time and chapters, via isolated `ffprobe`
  (#749).
- **Clean rebuild required.** Audible, Audnexus, AudioSilo and iTunes are
  keyless audiobook providers in Settings and setup; needs a `config-v7` file
  (#749).
- Malformed ASINs, invalid sidecars and contributors without a narrator role
  are no longer believed as audiobook evidence (#935).
- Prepared audiobooks no longer block scans in mixed-format libraries, have
  their own size limit, and are not re-read when unchanged (#936).
- The container check runs real-probe tests against the shipped `ffprobe`, and
  ADR-0061 records the audiobook provider posture (#929).

## 0.24.0 — Evidence economy and decided fields

- **Clean rebuild required.** Inventaire is a keyless ebook metadata and cover
  provider in Settings and setup; needs `config-v8` and new settings files
  (#883).
- Audiobookshelf can be reached over HTTP and the workspace served over HTTP or
  HTTPS; fresh deployments default to loopback HTTP under `config-v9` (#844).
- **Clean rebuild required.** Optional description advice is configured apart
  from cover review, with automatic application off by default (#892).
- **Clean rebuild required.** Titles differing only by decoration or accents no
  longer compete, and ebook titles are corrected apart from subtitles (#896).
- Automatic series fill refuses unproved work identity, keeps populated fields
  and never invents series numbers (#895).
- **Clean rebuild required.** ISBN, tag, description, author and language fills
  need proved work identity, and equivalent forms collapse into one choice
  (#897).
- **Clean rebuild required.** A sole confirmed ebook cover stays reviewable
  when sibling lookups time out, and is replaced only by a better-ranked one
  (#999).
- **Clean rebuild required.** Providers try ISBNs before title-and-author
  search and spend scarce quota where it helps; Library explains provider
  evidence (#882).

## 0.23.0 — Covers and responsiveness

- **Clean rebuild required.** The best confirmed cover is recommended without a
  reviewer; missing reviewer evidence reads unavailable, never clean (#864).
- **Clean rebuild required.** The owner can choose one optional cover reviewer:
  Codex device sign-in or an OpenRouter key and model (#872).
- Contained cover reviews stay within their process limit, and stale reviewer
  profiles are rejected at startup with a reason (#866).
- **Clean rebuild required.** One preparation run carries from setup into
  Library with stage progress, recent outcomes and reconnect state (#869).
- Owner login is remembered for up to 30 days and no longer waits on scan
  writes; upgrading signs existing owners out once (#862).
- **Clean rebuild required.** Google Books, Hardcover and Open Library requests
  are paced, waiting out bursts and resuming at the daily reset (#876).
- Activity reads stay responsive with immediate live updates, and deep Library
  and Attention pages stay reachable (#853).
- **Clean rebuild required.** An empty Subtitle fills automatically only when
  the book's own packaged metadata attests the provider value (#744).

## 0.22.0 — Full library

- First-visit owner-password setup and authenticated Settings cover
  connections, providers, libraries, limits, reviewer, server and appearance
  (#745).
- **Clean rebuild required.** Library offers owner choices only when an
  eligible alternative exists, and early works appear before preparation
  finishes (#830).
- Library links to readable per-work Evidence and History again (#829).
- Activity shows one row per change, searches displayed values, and survives
  one unreadable event (#788).
- Library explains terminal preparation failures, and preparation status stays
  available while preparation writes (#831).
- Library and Attention refresh as soon as an automatic capability is suspended
  (#794).
- Series memberships stated in an EPUB's package or front or back matter are
  recovered as evidence (#375).
- One malformed Audiobookshelf title no longer pauses a healthy connection
  (#797).

## 0.21.3 — Write-path truth and signal integrity

- A refused guarded write reports its real cause, startup recovery attempts
  every pending plan, and permanent refusals answer 409 (#677).
- **Clean rebuild required.** Provider-recovery and cover-stage failures name
  the actual fault, such as a full disk (#715).
- Type checking now covers tests and scripts, and test ports, mutation results
  and CI qualification report their real state (#693).

## 0.21.2 — Documentation economy

- The container declares a health check, caps memory and processes, and
  publishes on `127.0.0.1:8443` by default; LAN exposure is the owner's choice
  (#738).
- Moving between routes no longer takes keyboard focus the reader already
  placed (#760).
- Documentation and the changelog are smaller, with one authority per rule, and
  a gate checks links and entry length (#717).

## 0.21.1 — Engineering health

- Path-based security decisions use the routed path rather than the `Host`
  header (GHSA-86qp-5c8j-p5mr), and Starlette and FastAPI are upgraded (#702).
- Failed field-effect claims report their own cause, so `claim_conflict` means
  a real concurrency conflict (#656).
- Faults are no longer mistaken for ordinary outcomes such as a wrong password,
  and malformed responses are refused as invalid rather than crashing (#698).
- A nested repository call undoes only its own writes instead of committing the
  caller's partial work (#658).
- Library's attention line reads "1 field needs attention" (#705).
- The test suite runs on pytest, and the backend is formatted, type-checked,
  dependency-audited and verified as merged `main` and as the container (#661).

## 0.21.0 — Real-library correctness and recovery

- Library is the per-work decision workspace with provenance and Evidence
  inline, reversible browsing, search as you type, and every work reachable
  (#642).
- Covers show whole at their own aspect ratio, cover-reviewer advice is ready
  in review, and a recorded choice can be changed until it applies (#644).
- Changes reach the open tab as they happen, and Library and Activity offer
  `Enable writes` naming every destination it covers (#641).
- Activity says what each change did in the owner's words, and Policy no longer
  reports an activation the owner never made (#636).
- An automatic decision that keeps the current value is recorded as `No change
  required` without a write (#599).
- **Clean rebuild required.** Subtitle, series, ISBN, author and
  publication-date decisions need stronger evidence and keep truthful
  owner-facing reasons (#595).
- A lost Audiobookshelf connection pauses only its own work and resumes from
  the retained checkpoint; Library shows only completed review packages (#638).
- Library and Attention stay responsive at 2,000 works, and idle preparation no
  longer rescans the whole database (#802).


## 0.20.1 — Protected interface implementation

- The protected Library review workflow opens on the first reviewable work.
  Choosing between the current and a proposed value takes one step, and a clear
  review-complete point comes before the next work. Recording a decision stays
  separate from applying it.
- The Library policy notebook shows one active policy and one draft in four
  chapters. A single action validates, snapshots and previews the whole draft
  before a reversible activation.
- Policy impact previews count only real changes and show each chapter's exact
  changed-field count. Each policy option explains its source, effect and
  abstention boundary in place.
- The owner-only Activity ledger is a paged, date-bounded and searchable record
  of review decisions, policy changes, safeguards, guarded writes and
  verification.

## 0.20.0 — Complete qualified ebook metadata decisions

- The metadata policy gains a rich-field vocabulary for manual preview:
  subtitle, series, list operations, bounded description text, and exact
  ISBN/ASIN choices.
- Series, exact ISBN, genres and tags, and safe plaintext descriptions can be
  set to decision-only Automatic. Tags are off by default, and every other rich
  option stays manual.
- Subtitle is not qualified for Automatic, `explicit` stays human-only, and
  narrators and cover are excluded.
- Rich metadata evidence is kept for manual review without widening automatic
  authority.

## 0.19.0 — Owner-confirmed semantic cover decisions

- The installed review product supports owner-confirmed cover decisions. A
  cover field shows the current cover and up to 12 safe alternatives, and the
  owner can keep it, pick an alternative, or take no action.
- When configured, the contained cover observer and the deterministic cover
  policy run as advisory evidence only.
- The source-only cover workbench is consolidated into one maintainer review
  package.

## 0.18.0 — Semantic cover decision workbench

- A source-only contributor workbench classifies each retained cover's identity
  as verified, plausible, unknown or conflicting. It makes no provider calls
  and has no write authority.
- Report-bound covers become deterministic, metadata-free PNG previews that are
  byte-identical across platforms. They pass to a sandboxed contained reviewer
  under fixed size limits.
- A small, closed vocabulary of visual observations feeds a deterministic cover
  policy. That policy can avoid promotional text or tie-in branding and can
  prefer a minimal composition. Unbound or invalid results fail closed.
- The contained reviewer retries one known transient CLI abort once, and every
  other failure stays terminal. Exact-head pull-request CI jobs run only for
  candidate commits.

## 0.17.0 — Interface fidelity restoration

- The authenticated interface returns to its Variant M design: an editorial
  shell, one Library destination with a paginated Attention queue that puts
  attention first, and phone layouts that keep the same information.
- Review detail records a decision for the current value or a specific value
  directly, with no submit step. If a response cannot be reconciled, the fields
  stay blocked until a fresh read.
- Policy is a notebook that shows one chapter at a time (Identity, Publication,
  Curation, Unsupported content). Activation follows an explicit sequence:
  validate, preview, impact, activate.
- Activity is again a readable guarded-work ledger, with explicit consent and
  readiness, scoped pause and revoke, and grouped recovery.

## 0.16.0 — Release-candidate simplification

- Library, Attention, review detail, Policy and Activity run in one persistent
  authenticated React shell at their existing URLs. Node is excluded from the
  wheel and the container.
- The only installed command is the authenticated `review-serve` launcher.
  Catalogue, report, batch, proposal and contained-reviewer tools move to a
  source-only contributor workbench, and the retired shadow-review workflow is
  removed.
- Browser workflows go through one application boundary of named operations.
  Unconfigured controls report `unavailable` instead of a persistence failure.
- Activity consent covers the eligible fields when others are unavailable, and
  it names the exact destinations. A returned automatic field stays on manual
  hold until the requested scan completes.

## 0.15.0 — Continuous guarded application

- Explicitly consented Audiobookshelf libraries receive continuous guarded
  application. Human and qualified automatic decisions share one 14-destination
  kernel with exact-field value guards, pause and revocation, durable manual
  holds, and restart recovery.
- Each Audiobookshelf write reads current state first and is checked by reading
  it back. An authentication failure suspends the connection, and a permission
  or field fault suspends only the affected destination.
- Audiobookshelf has no compare-and-swap, so a documented same-field
  lost-update window remains.
- The single container has startup, readiness and liveness checks, graceful
  draining, scoped incidents, and previewable redacted support bundles. It runs
  non-root with one volume, one HTTPS port and read-only media.

## 0.14.0 — Quiet background metadata decisions

- The owner can ask for a library to be prepared progressively in the
  background. Scans run at first start and on an interval through a persisted
  two-lane pipeline that survives restarts, still decision-only and write-free.
- The metadata policy has a draft, a preview and an activation step. An exact
  reviewed draft activates in one atomic cutover, and affected cases return
  through review.
- Qualified policy defaults create decision-only automatic dispositions. Owner
  pause, safety suspension and recovery keep unresolved fields in human
  Attention.
- The shell gains a compact activity popover with pause, resume and retry.
  Public CI is split into fast-source, installed and browser jobs behind one
  gate.

## 0.13.0 — Authenticated human-exception review

- `review-serve` is an HTTPS-only authenticated review server with a validated
  certificate, an owner-only key, opaque SQLite sessions and Argon2id
  verification.
- The owner can browse Library lenses and an exact-field Attention queue. They
  can record `accept`, `keep_current` or `reject` on an unresolved field, and
  the decision and its audit event commit together.
- Each case, Library page and Attention page is read from one consistent
  snapshot. Evidence loads in pages, and a retried decision replays its
  original acknowledgement.
- The interface follows the Variant M design from phone to desktop widths. The
  theme is kept in a presentation-only cookie.

## 0.12.0 — Shadow decision qualification

- `shadow-decision-preview` produces one deterministic decision per proposal
  for a current ReviewCase. It chooses manual on ambiguity, conflict or
  incomplete evidence.
- A fixed catalogue of allowed shadow-review actions with exact citations is
  built before any provider call. Insufficient or out-of-catalogue support goes
  to manual review.
- New field policies cover several fields: - publication dates, kept at their
  strongest supported precision - language and publisher, with ISO 639 aliases
  and an acceptable-choice outcome - dependent title, subtitle and series
  combinations
- The qualification reviewer runs with disposable provider state, kept apart
  from the primary credentials, and accepts reviewer JSON with minor formatting
  differences.

## 0.11.0 — Reviewer evidence quality

- Google Books joins Hardcover and Open Library as a report-only metadata
  provider. Each distinct valid local ISBN family is searched in its own lane,
  alongside one title-and-author lane.
- Provider failures use one closed, safe failure taxonomy. Narrowly classified
  transient failures retry within fixed bounds, and Hardcover GraphQL errors
  count as unavailable, never as success.
- EPUB evidence recovers contributor roles, publication dates, subtitle,
  publisher and imprint from front and rear matter. ISBN recognition is
  stricter, and an `Unknown` creator never replaces the authors on its own.
- Publication dates keep their precision and compatibility. Titles and creators
  that differ only by closed normalizations (case, whitespace, punctuation,
  `Family, Given`) count as agreement.

## 0.1.0 to 0.10.0 — Foundations

- Configuration names explicit Audiobookshelf connections, libraries, read-only
  mounts and provider connections. A read-only current-book catalogue supports
  bounded refresh and inspection.
- `metadata-report` builds immutable, replayable evidence reports from
  Audiobookshelf, an isolated EPUB inspector and Hardcover, stored append-only
  in SQLite. The runtimes from before 0.4 are removed.
- EPUB evidence keeps the ordered package, opening-matter and bounded
  rear-matter evidence, duplicates, and publisher and imprint roles.
  Book-producer credits are excluded from contributors.
- Each ISBN claim is classified as accepted, associated, unrelated, invalid or
  unresolved. Acceptance is claim-local, and collisions between provider works
  stay unresolved.
- Open Library is an independent evidence provider. Report-only cover evidence
  and a bounded cover gallery keep private, content-addressed originals and
  previews.
- Catalogue evidence batches support preview, immutable manifests and serial
  execution. An Open Library batch is limited to five entries.
- `proposal-set-create` creates immutable, explainable proposals for the 16
  Audiobookshelf destinations. Review admission keeps exact-current cases, and
  a qualification-only, sandboxed Codex shadow-review path is added.
- Databases accept only an absent or the exact current schema, with no
  migrations. Installed-wheel qualification builds and tests from the exact
  source.
