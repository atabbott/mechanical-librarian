# Changelog

## Unreleased

- The masthead holds still as its status changes and scrolls away once taller
  than a quarter of the window; Library opens with its write handoff in place,
  and a Library read that fails while it is still opening is retried once,
  silently. Words break only where they must. At 200% text, Activity rows,
  Policy and Settings controls and setup refusals stay in view. Saving
  settings moves nothing, loading reads as one quiet line, Library stays
  current on reviews and Attention, and each Activity Details names its row's
  time (#1291).

- **Clean rebuild required.** A library can declare the languages it holds in
  its configuration file (`languages`, first preferred). Audible is then
  searched in those languages' stores first, and an audiobook that states no
  language is never matched to an edition in another language. An edition
  whose runtime agrees but whose language or cut contradicts the recording is
  no longer matched as it (#1315, #1305).

- **Clean rebuild required.** An audiobook whose search admitted no recording
  is now searched again once a week, because Audible's search can list a
  recording it missed before. An open review case is replaced only when the new
  search admits a recording, so an unchanged answer leaves your queue alone
  (#1451).

- Review choices are one radio group: arrows move between them, Space or Enter
  records. Field tabs say their status in words. A cover records in one tap,
  states its pixel size and every source, and a duplicate image is one choice.
  Works show the cover the library holds, read only by the digest their report
  retains, and initials use letters (#1290).

- **Clean rebuild required.** An audiobook whose Audible edition agrees on
  runtime but whose title differs in shape (a joined subtitle, a bare leading
  number, a series-position title) can now be matched when its narrator or
  series number also agrees and nothing contradicts it. A bare "NN Title"
  folder name is also searched without its number, a lone typographic
  apostrophe between letters reads as a straight one, provider whole-minute
  runtimes count as floors when compared with the 90 s band, and products a
  lookup refused are kept with their details. The cold-start report's "no
  comparable runtime" class is now "tight product refused by title" where that
  is what happened (#1307).

- Setup now lists iTunes with the book and ebook providers and suggests it only
  for libraries with ebooks. No audiobook is sent to iTunes, so it was never
  of use to an audiobook library (#1458).

- **Clean rebuild required.** Once an audiobook edition has been matched, the
  next preparation of the same tracks looks it up directly before searching,
  so a search that no longer lists it cannot lose it to a sibling edition. One
  recording's regional ASINs now offer one ASIN choice (#1316).

- Text and field edges meet WCAG AA contrast in both themes, no text is below
  12px, and every disclosure and authority control is a 44px target. Focus no
  longer falls to the page after a command, failed retry or read failure;
  Attention focuses its heading, holds focus as a tablet drawer and returns it
  on Escape, and Tab closes a dropdown onto its trigger. Headings keep one
  outline and Activity reads each change as a sentence (#1288).

- Setup now describes iTunes as what it is since audiobooks stopped being sent
  to it: a check of an ebook's title and author against iTunes audiobook
  listings. An audiobook inspection that states no recording facts now fails
  that item instead of being planned without them (#1303).

- **Clean rebuild required.** An AudioSilo answer whose recording lists more
  regions than the ten admitted marketplaces now keeps the queried ASIN's own
  entry instead of failing as malformed. Malformed, oversized and
  candidate-limited responses are named apart, an AudioSilo lookup miss is no
  longer a failed request, and an over-bound chapter document no longer starves
  the next lookup (#1308).

- Settings can now test, save and refresh an OpenRouter description model
  whose catalogue entry has no request fee. The fee is shown as Unavailable;
  a present invalid fee is still refused. Automatic description application
  no longer admits or writes a rewrite that is a placeholder, mostly quoted
  praise, cut off or marked up; you can still choose it yourself. No clean
  rebuild is needed (#1400).

- Setup and sign-in failures now say what went wrong: an unresolvable name, a
  refused connection, an unverified certificate, a timeout or a rejected key,
  naming the address and one remedy. Setup suggests providers per library and
  confirms each step; sign-in clears a rejected password, explains a forgotten
  one and opens in your theme with the workspace type (#1287).

- Hardcover and Open Library now send their requests through the same bounded
  request path as every other metadata provider. Their requests, limits,
  retries and reported outcomes are unchanged (#984).

- When the review library cannot read the provider-recovery schedule, the
  service log now gets the same one-line JSON warning as every other
  operational event; before, its code and exception type were dropped (#1447).

- A managed Node or Python update now moves the Dockerfile, workflow and, for
  Node, frontend `engines` pins together in one Renovate pull request, instead
  of moving the dated pins alone into a build that could not pass. A new Node
  major line or Python minor version waits for approval (#1448).

- **Clean rebuild required.** Automatic authority now binds only the operations
  each qualification permits, so ordinary source changes no longer rotate it.
  Automatic decisions recorded under an earlier qualification are refused
  rather than kept as decode-only history, outcomes move to
  `shadow-outcome-set-v11`, and the qualification-state bridge leaves the
  installed CLI for `scripts/` (#1396).

- The activity projector's exit events now reach the service log as value-free
  JSON lines on stderr; before, its `info` events were silently dropped. A
  never-shown owner-settings log line is removed (#1304).

- An OpenRouter model is validated however large OpenRouter's model list grows,
  and a reviewer check or review shares one time limit across its requests,
  stopping at its first read past it. A too-large answer, a timeout, a
  data-policy refusal and too many images each get their own reason and remedy
  in Settings and setup, and a failed cover review says what to change
  (#1330, #1331, #1332).

- A managed uv update now moves the CI workflow and container pins together in
  one Renovate pull request, instead of moving only the workflow pin (#1177).

- Once a provider failure has earned a wait before its next retry, a later
  throttle can lengthen that wait but no longer shorten it. A one-second Google
  Books pacing hold carried by a retried report no longer brings the next retry
  forward from hours away, so a pacing pause no longer uses up a recovery
  attempt (#1318).

- The shared review test double now satisfies every port it is bound into (#780).

- A review case's history now shows the same owner decisions, description
  advice and description selections on every read when several share one
  second: each is cut to the history limit in the order it is displayed, rather
  than in whatever order the database happened to return (#1209).

- The Audiobookshelf field-effect canary runs end to end again: its synthetic
  catalogues no longer split title and author evidence, and its cover accept is
  pinned to the decision contract (#1427, #1185).

- The workspace opens in your saved or system theme without a light flash,
  uses its documented Inter typeface (self-hosted, OFL), and its main actions,
  including Confirm write permissions, share one filled style; status chips and
  layout breakpoints follow one design (#1289).

- Labels, states and reasons now read in plain words everywhere; running times
  in hours and minutes with the real difference; languages by name; Settings
  sizes in MB and polling in minutes; one date format with relative times. A
  first provider throttle is no longer reported as calm, and an owner-entered
  change names the value it replaced (#1286).

- A description written to Audiobookshelf is now recorded as verified when the
  service stores it escaped, such as `&` as `&amp;`. Current, provider and local
  file descriptions are all read as the text they show, so an escaped one is no
  longer proposed again as its own text and escaped markup never becomes a tag.
  Keeping a formatted description after a change is refused rather than written
  back flattened. Clean rebuild required (#1424).

- Opening an Activity row of checks that changed nothing now shows one row per
  field it names, with each field's recorded decision inside that field's
  Details. A Library search now reads "Found 3 of 24 works", counting the
  selected lens against its whole-library total; screen readers hear the lens
  named after it (#1425).

- A change decided while Audiobookshelf could not be reached is now written
  once the connection is back, without another action, instead of waiting for
  the next decision or restart. It is checked through its own book until then,
  nothing is sent before it, and it uses none of its retries (#1423).

- Works no provider matched now have their own Library lens, Not matched, with
  a count and one reason each: found but not this work, providers unavailable
  (saying it will be checked again only when a retry is scheduled), or no
  provider matched. They no longer count as No attention needed, and the
  masthead and run summary name them. Clean rebuild required (#1407).

- A write in progress now completes when the system clock steps backwards,
  for example after a time correction or a resumed virtual machine, instead of
  being refused partway and left waiting for a restart. Its history keeps the
  later time, so events never read as going back in time (#1386).

- A store holding a row outside the schema's own value rules is now refused
  when it is opened, instead of passing because SQLite skips those rules when
  it checks a read-only connection. A refusal never includes a row's values
  (#1401).

- A write Audiobookshelf could not be reached for is now prepared again once
  the connection is back, at most three times, instead of being dropped, and a
  write that had already landed is confirmed without sending it twice.
  Activity says the change is waiting, or why it stopped, and a write
  interrupted before sending reads as not sent. Deciding a field whose write
  waits for a retry now replaces it without sending it (#1402).

- Wrong passwords from one client now slow only that client, so a stranger can
  no longer keep the owner from signing in elsewhere; a backstop still limits
  all clients together. Setup asks for an owner password of at least 12
  characters and refuses a shorter one on that field; existing passwords keep
  working. Clean rebuild required for the session database only, which signs
  every browser out once (#1384).

- The masthead and the Library run summary now count preparation in the same
  words: ready to browse, recorded with nothing to decide, and could not
  finish, as the run's books stand now, so the two agree. A library outside the
  current run is marked as not in it, and first-finish history reads Prepared
  (#1366).

- Retrying a failed Attention queue now returns focus to the Attention rail
  instead of leaving it on the page (#1399).

- Moving to another field after a first-run setup refusal now keeps focus
  there, instead of a late frame pulling it back to the refused field (#1406).

- Claiming a new instance now needs the setup claim token the container prints
  once in its log and keeps in `/data/private/setup-claim-token`, so a network
  visitor who reaches the port first can no longer take ownership. Each start
  replaces the token until the instance is claimed. Clean rebuild required for
  incomplete setup only (#1383).

- A timeout or dropped connection during an Audiobookshelf write now holds
  writes only until the connection is seen healthy again, instead of
  suspending writes to that field for good. A write whose read-back failed is
  confirmed by the running worker, and a write interrupted before it was sent
  is rechecked against every safety condition before it is retried (#1382).

- An automatic decision to keep an ebook's filled ISBN is now recorded as a
  kept field and joins its preparation's "Checked N fields, no changes" row,
  instead of reading "Details unavailable" when no source proposed a matching
  ISBN (#1368).

- When an ebook's source first throttles or fails lookups, Library and Settings
  now say why and, for a Google Books throttle without a key, offer the key
  link, without waiting for a retry. Both stay until the source recovers or
  Retry now starts over. Activity names the same reason when the lookups are
  enrolled for retry, and no longer says a recovered source needs correction
  after Retry now (#1367).

- Activating a policy now keeps each library's write permission, and the
  Activate step names the libraries that keep it. A permission that lapses is
  shown with its reason in Library and Activity, and Activity no longer reads
  as calm while no library holds permission. Clean rebuild required (#1370).

- A policy activation that completes with failures no longer blocks Validate
  and preview, and a new activation now proceeds over it. After activation,
  affected works leave Attention and show in Library without a case until they
  are re-prepared under the new policy. Retry failed reconciliation counts a
  book whose evidence moved to a newer report as reconciled, and retried work
  always resumes, so retry and Dismiss reach a clean end (#1369).

- The selected work now stays on screen with its fields after you enter a
  value, choose genres, change or withdraw a decision, after a search that no
  longer matches it, and when a link opens a work outside the current page. It
  is called unavailable only when the work itself cannot be read (#1365).

- Where no proposed value fits, you can now enter a title, subtitle or
  publisher yourself, with suggested clean-ups, or choose which genres to keep.
  A recorded decision can be changed or withdrawn, and after a write the value
  it replaced is offered as Previous value to choose again. Your hold on
  automatic handling now follows the work when it is re-read. Upgrading
  requires a clean rebuild (#1285).

- Library counts now come from one library-wide summary and name their unit:
  the Attention badge counts works, Works reads "Found 1 of 20 works" under
  a search, and no count shows 0 while loading. Attention lists each work once
  with its fields as rows, preparation counts say they are for this run, and
  selecting a work no longer moves the workspace (#1284).

- Library and Settings now show which sources are limiting or refusing
  lookups in each library, how many lookups wait, and when they retry next,
  with a link to add or check a key. Retry now raises the recovery generation
  by exactly one, replacing the number in Settings. The masthead stays at idle
  so Scan now and Pause are always reachable (#1282).

- Activity now gathers a preparation's automatic no-change checks into one
  "Checked N fields, no changes" row that opens onto each check, so the
  changes lead the page. Automatic choices name their field and value, write
  permissions spell ISBN and ASIN as the ledger does, an unknown filter link
  says it showed all events, and page buttons never split a word (#1281).

- Reviewing write permissions on Activity now keeps the checked libraries and
  ticks through refreshes, focus changes and updates from other tabs. They are
  cleared, with the reason stated, only when the library settings really changed,
  which Confirm checks again before granting. The panel sits below the heading,
  and there is one Review write permissions entry, never inside a disclosure (#1279).

- An author or narrator that only loses its accents now keeps your spelling;
  any other accent-dropping or credit-prefixed contributor, a title carrying a
  "Now a Major Motion Picture"-style announcement, or a description that is
  mostly quoted praise is never written automatically or recommended; each
  stays your choice. Upgrading requires a clean rebuild (#1283).

- Policy's impact preview now marks a changed rule it did not evaluate, such
  as Language, "Not previewed" instead of reporting no change. The page states
  each status once, hides zero counts, names an activated edited policy
  "Custom policy", shows library display names, focuses the success message
  after Activate, and clips or splits no label at 320 px (#1280).

- A decision or policy validation made after another tab or the setup handoff
  refreshed the session now succeeds on the first press; other actions say
  "Your session was refreshed. Press again." instead of a generic failure (#1018).

- Checkpoint numbers now state what each checkpoint delivers, and the history
  is renumbered once; the former numbers are mapped in work management (#1321).

- The frontend test runner moves to Vitest 4.1.12, clearing advisory
  GHSA-qxg6-89mg-rqv8 that failed the npm audit gate (#1321).

- Hardcover and Open Library no longer fail on an audiobook identified only by
  its ASIN, which neither can look up. They now send no request and record that
  they had nothing to ask, so the audiobook is no longer marked provider-limited
  and no provider-health failure or recovery job that could never succeed is
  created. No clean rebuild is needed (#1306).

- Provider request failures, throttles, deadline stops and recovery-job
  transitions now write one value-free JSON line each to the service log. A line
  never carries a title, query, URL, credential, path or item id. The cold-start
  report moves to v4 and counts replays, starved providers, refusals, decisions,
  recovery events and stored-authority attention. Requests, reports and
  decisions are unchanged, and no clean rebuild is needed (#1299).

- An audiobook whose title carries a series decoration that the Audible
  recording's own series confirms, a series name in parentheses or a leading
  "Saga Book 2 -", or whose author is written with dotted initials, now matches
  that recording when its running time agrees within 90 seconds. A wrong book, a
  dramatisation or a translation still does not match. Upgrading requires a
  clean rebuild (#1260).

- Activity loads again after a verified write of a long value such as a book
  description; the confirmation now reads "Matches the new value" (#1278).

- Each audiobook report now records why every provider lane ended: admitted,
  empty, refused, overflowing, unavailable, out of time or never sent. It also
  records how far Audible's search went and the nearest same-title runtime that
  was too far off. The cold-start report counts these outcomes per provider.
  Nothing it records changes a request or a decision. Upgrading requires a
  clean rebuild (#1258).

- An audiobook whose exact recording is unresolved now also offers its series,
  series position and a cleaned-up title from matched book evidence. A
  description or genre choice that only trims or extends the current value stays
  choosable without counting as needing attention, unless that field is set to
  Ask. A series named like a title set to Ignore is no longer refused.
  Upgrading requires a clean rebuild (#1253).

- Audiobook descriptions over 512 characters now show as oversized instead of
  missing, placeholder languages such as `und` and the `prID` tag no longer
  count as evidence, and credit lines, format words and genre lists such as
  "Audiobook" no longer pass as real audiobook descriptions or genres. Upgrading
  requires a clean rebuild (#1250).

- Automatic audiobook metadata no longer drops a series from a title unless the
  series is kept or filled, no longer replaces narrators with a cast that shares
  nobody with the file's own credits (both now ask you), and records authors in
  another order as unchanged instead of rewriting them. Upgrading requires a
  clean rebuild (#1252).

- Audiobooks now enter the attention queue under the same conditions as ebooks:
  tag and ASIN fills never count as needing attention on their own, and
  narrators do only under "Authors and narrators: Ask me". An uncounted fill
  stays in its case as an optional choice to keep the current value or choose a
  proposed one. Upgrading requires a clean rebuild (#1249).

- Library descriptions containing HTML closing tags such as `</p>` or Unicode
  line separators are now read and compared instead of being marked malformed,
  for ebooks and audiobooks. Real paths, credentials and controls are still
  refused. Windows-1252 garbling is refused with its own internal reason but
  still shows as malformed in reports and the UI. Upgrading requires a clean
  rebuild (#1251).

- One slow or unavailable metadata provider now uses at most 20 seconds of an
  item's 60-second provider window, so the providers after it and alternative
  cover downloads still run instead of being recorded as out of time, for
  ebooks and audiobooks (#1248).

- Audiobooks are now looked up only with Audible, Audnexus, AudioSilo, Open
  Library and Hardcover. Google Books, Inventaire and iTunes no longer spend
  requests or recovery work on them. Ebooks are unchanged and no rebuild is
  needed (#1247, #1254).

- An audiobook Audible refuses on title but whose running time matches the
  recording's is now looked up by its ASIN on Audnexus and AudioSilo, and kept
  only when that answer names the same book. A looked-up answer naming another
  book no longer suggests its title or counts as a recording match. Upgrading
  requires a clean rebuild (#1257).

- An Audible audiobook search that finds many products now sets aside those
  whose running time is more than ten minutes from the recording's, so a popular
  title no longer ends the search as too many matches. Upgrading requires a
  clean rebuild (#1256).

- An audiobook Audible cannot find by its recorded title is searched again by
  the title in its folder name or single file's title tag, and a recording found
  that way is kept only when its running time matches. Upgrading requires a
  clean rebuild (#1255).

- Audible and iTunes audiobook searches keep trying shorter title forms when
  they find only books that fail the identity check. Those checks are counted as
  refusals, not as empty searches. The `schema-adopt` command is removed, and
  upgrading requires a clean rebuild (#1245).

- A failure while preparing the setup or store runtime at startup now reports
  the original error instead of an unrelated internal one, and the database
  connections opened for startup are still closed (#1235).

- Library's write-ready notice appears with the first rows after a restart
  again, about 0.1 s after login instead of about 6 s at 8,000 decisions: it
  reads only the consent state Activity's consent section states, from the same
  projection, and no longer waits for Activity history to catch up. What it
  offers, and Activity, are unchanged (#1236).

- The workspace, sign-in and setup now share one theme rule: a saved choice,
  otherwise the system's light or dark setting. Library puts write readiness
  and Attention in one toolbar; phones get a one-row navigation, readable small
  text and no letter-by-letter wrapping; routine update, reviewer, preference
  and Activity copy is shorter (#1013).

- Library, search, the attention lens and Attention spend less time on each
  work's cover check: what each stored cover check says is read once per
  process and reused while its bytes are unchanged, while provider and reviewer
  state are still read on every request. At 8,000 decisions Library answers in
  about 45 ms instead of 55 ms. Results, order and refusals are unchanged (#1237).

- Activity opens in about 1 s after a large batch of new history, and a restart
  answers in about half the time (12 s instead of 23 s at 8,000 decisions):
  catch-up runs in the background and checked reports are kept, costing about
  70 MiB more memory. The first Activity read just after a restart is slower,
  5-7 s instead of under 0.1 s, as it finishes catch-up itself; catch-up also
  uses CPU while Activity is closed. Results and refusals are unchanged (#1231).

- Library, search, the attention lens and Attention no longer re-check every
  field on each request: which fields ask for the owner is kept current as each
  write lands. Results, order and refusals are unchanged. Adopt a database from
  the previous build offline with `schema-adopt review-attention-facts-v1`
  instead of rebuilding it (#1232).

- Opening Activity after a large batch of new history no longer makes
  preparation or owner writes fail with "database is locked": the catch-up now
  proceeds in short reads that let writes through. That first read takes
  longer, about 12 s instead of 8-9 s at 8,000 decisions. Results, counts,
  order and refusals are unchanged (#1225).

- Preparation slows far less while Library or Attention is open: an active tab
  refreshes its Library page and attention at most once every 1.5 seconds while
  works keep arriving, instead of once per prepared work, and reads once more
  after the last. Stale data is still marked at once and navigation still reads
  it immediately; results and refusals are unchanged (#1224).

- Activity and permission readiness no longer recount pending changes, or pick
  the readiness example again, on every read: each reader reuses them until the
  database changes. The fresh consistency check still runs on every readiness
  request, and counts, the example and refusals are unchanged (#1223).

- Library, search, the attention lens and Attention answer faster on repeat
  visits: each work's case and report are checked once per process and reused
  while their stored bytes are unchanged, instead of on every request. Pages,
  order, paging and refusals are unchanged, and the reuse is bounded (#1226).

- Activity keeps up with each change at scale: a change re-reads only the
  history of the events it affects instead of all history for each one, and a
  large refresh parses each report once. Results and counts are unchanged;
  events tied on time and identifier now order the same way on every read path,
  and one invalid stored outcome no longer refuses Activity reads that do not
  show it (#1218).

- Library and Attention stay responsive while preparation runs: Activity now
  reads only the history added since its last check, and re-verifies older
  history a slice at a time, instead of re-reading all of it on every change
  while preparation waits. Activity results, counts, order and refusals are
  unchanged (#1200).

- Library, search, the attention lens and work detail no longer slow as owner
  decisions accumulate: each row's case is projected once for all of its
  decisions, superseded revisions included, rather than once per decision in
  the store and again in the page. Every decision is still validated, and rows,
  order, paging and refusals are unchanged (#1215).

- Preparation no longer stops until a restart when a long Activity read holds
  the database past a claim's busy timeout: a claim that committed nothing
  retries on its next one-second wake and preparation resumes once the read
  ends. Every other claim failure still stops preparation (#1216).

- Library and work detail stay within their read budget on the SQLite the image
  ships, and on newer SQLite once any field-effect suspension exists: each row
  reaches its case's lineage and package by index instead of walking every
  work. Rows, counts, order and refusals are unchanged; clean rebuild required
  (#1211).

- Permission readiness and the pending field-change list and counts now grow
  with decision history alone rather than with decisions times current reviews:
  each request finds the cases holding a current review once instead of once
  per decision. Which changes are pending, their order, paging and refusals are
  unchanged (#1205).

- Activity, the scheduler's claim, owner decisions, review history and
  description advice no longer slow as applied changes accumulate: they reach
  the field effects they ask about by index instead of scanning every effect.
  Answers are unchanged, except that description application history ties
  within a second are now broken deterministically, matching the displayed
  order; clean rebuild required (#1204).

- Library, Attention, search and work detail do less work per request and no
  longer slow as decisions and policy changes accumulate: indexes reach that
  history directly, cover currentness is evaluated only where it can change a row
  or count, refusal scans skip text that cannot match them, and a read reuses
  documents it already validated. Rows, counts, order and refusals are unchanged;
  clean rebuild required (#1202).

- Library, Attention and search choose their rows and counts from stored per-work
  title, author, format, provider and policy facts, reading only the reports they
  show instead of every work's report and proposal on each request. Visible rows,
  counts, order and matches are unchanged; clean rebuild required (#1199).

- Library, Attention and work detail answer faster: browse rows read only the
  fields they show, each under its existing validation, and a work's detail no
  longer re-reads its stored report for every consumer. Displayed values,
  counts, ordering and refusals of displayed values are unchanged (#1191).

- Settings keeps one draft across seven sections with true per-section counts, a
  shared review-and-save tray, Discard all, and validation that reaches hidden
  sections. Policy and Settings share segmented controls, disclosures, counts and
  the tray; reviewer models get labelled pickers and per-book cost examples (#1034).

- Library and Attention keep their current rows in place, dimmed and marked
  busy, while the next page, lens or search loads, and never show rows from a
  read that has been superseded or failed. Hovering or focusing paging, lenses
  and works prefetches them as background work (#1192).

- Reloads and return visits reuse the browser's private copy of the review
  application and of cover previews instead of downloading them again, and the
  application is served precompressed. The shell and every other API response
  remain uncacheable (#1193).

- Settings and setup show a measured Luna cover estimate of about $0.6364 per
  1,000 review requests. Reviewer cost evidence now records its measured run on
  each row, so rows from separate complete runs ship together truthfully (#1180).

- Cover considerations retain exact canonical report references instead of duplicate
  report JSON, preserving all evidence, history and effect authority. Clean rebuild
  required for prior application stores; no migration or cleanup is added (#1158).

- Provider recovery retains bounded request and failure diagnostics while keeping
  completed work available. Activity explains current provider waits using the
  existing retry eligibility timestamp. Retry pacing is unchanged; existing
  recovery stores require a clean rebuild (#1157).

- Review supported audiobook descriptions and genres even when the exact
  recording is unresolved. Source-backed owner choices use existing guarded
  application; automatic, cover and other-field authority remain unchanged.
  Existing installs require a clean rebuild (#1166).

- Preserve acquired current covers when metadata providers exhaust the shared
  deadline, and restore supported declared EPUB covers in validated archives
  containing directory entries. Timeout labels distinguish requests that never
  started from attempted reads; owner confirmation and currentness guards remain
  unchanged (#1156).

- Clarify audiobook evidence-only outcomes and conditional retry guidance (#1155).

- Activity reuses validated readable history across runtime generations, keeping
  search and paging responsive while preparation continues. Exact source-byte
  checks preserve corruption refusal and current results without a schema change
  or changes to write permissions. Compact Activity rows keep category labels
  clear of their details controls. New search text survives earlier search
  responses (#1150).

- Library and Attention search avoid repeating package matching for every review
  field, keeping searches responsive while preserving current results, counts,
  ordering, and unavailable-state checks (#1160).

- Connection checks refuse promptly while startup recovery is incomplete or has
  failed. After recovery, an explicit retry preserves the connection result and
  existing review history (#1153).

- Audiobook provider recovery now checks unchanged audio with the isolated
  inspector and completes ordinary review preparation; changed audio stops
  before provider dispatch (#1151).

- Library and Attention reuse each browse query's filtered population for its
  page and totals, reducing repeated work on populated libraries while preserving
  current decisions, search, ordering, and unavailable-state checks (#1148).

- Activity history remains available while preparation continues. Write-permission
  review checks readiness on demand and requires a fresh review after known changes,
  without changing consent or guarded-write checks. The Activity API is v3 (#1144).

- Activity reads select the requested page before inspecting report labels and
  reuse fully validated reports during each fresh read, keeping preparation
  responsive without changing history or write permissions. Preparation progress
  stays visible when another configured library has never been scanned. Shutdown
  retains preparation dependencies until accepted reads finish (#1142).

- Library and Attention recover from one provider-fetching transition during a
  read. Repeated changes or changed authority show the existing unavailable
  state with manual retry. Exact qualification bindings change; prior derived
  application stores require a clean rebuild (#1140).

- Exact supported incumbent titles remain unchanged without suspending title
  automation or requesting owner attention (#1138). Clean rebuild required.

- Unclassified preparation failures emit bounded, value-free service diagnostics
  correlated with operations references, without changing recovery or retries.
  Prior derived application stores require a clean rebuild (#1136).

- Qualified audiobook title fills and replacements persist without suspending
  automatic title handling. Existing consent and guarded application remain
  required; prior derived stores require a clean rebuild (#1134).

- Conflicting ISBN-linked titles stay available for owner review even when most
  sources support the current title, without disabling automatic title handling.
  Existing installs require a clean rebuild (#1132).

- Settings shows the accepted measured Luna description request cost.
  The bounded reviewer collection report preserves incomplete phases, unknown
  charges and independently inspected sample limitations. Contained measurement
  admits the configured model; opt-in stop-on-failure retains failed attempts
  and known charges (#1060).

- Description cost measurement and advice from an already configured model work
  when OpenRouter omits its catalogue request fee. Actual charges still govern
  measurement budgets; Settings continues to require complete prices (#1129).

- Preparation progress stays consistent when a library connection becomes
  unavailable, so queued work remains visible and the app can reopen safely
  without stale running counts. Recorded automatic decisions remain readable
  as history without granting current authority (#1127).

- Browser CI allows 15 minutes for setup and the existing bounded tests (#1069).

- Setup and Settings separate observed reviewer rates from measured request
  estimates, or show not measured. Validation stays visible before continuing.
  An explicit owner tool captures private, source-bound cost evidence with a
  monitored budget; no measurements ship yet. Clean rebuild required (#1069).

- Cover setup and Settings recommend GPT-6 Luna for advisory observations,
  with visual-only format guidance, data-collection denial and retention
  disclosure. Custom models and exact model-labelled history remain available;
  known observation errors do not grant automatic cover authority (#1124).

- Owners can explicitly confirm current retained covers with an unconfirmed match
  or lower visual quality, and revise their choice before application begins.
  Contradictions remain refused; history retains the observed match status.
  Interrupted acknowledgments stay uncertain until the current choice is reloaded.
  CLI choices require explicit confirmation and revision baselines; reviewer changes
  preserve fresh choices and retained-cover keeps; active searches stay non-actionable. Clean rebuild required (#1078).

- Supported containers supply the contained reviewer profile for optional cover
  setup and description advice. Wizard and Settings distinguish provisioning,
  host and sign-in readiness, support recovery, and show the admitted model.
  Reviewer availability stays separate from permission to apply changes (#1009).

- Policy preview shows active-to-draft changes from one retained snapshot, with
  separate ebook/audiobook selected behavior and sample counts. Activation checks
  the captured policy generation. Write permission opens one scoped review that
  stays closed after cancellation or success; history distinguishes recorded
  choices from verified effects and shows event times (#1011).

- Ebook cover correction: “Replace if strictly better” fills a known-empty
  cover using existing deterministic admission; it does not replace a confirmed
  ebook cover. Its v3 audit records the actual option; clean rebuild required.
  Fixed-source upgrades and audiobook replacement stay unchanged (#1011).

- Find books by configured library names and retained titles or authors even
  when preparation fails. Library shows actual field observations, distinct
  source roles and truthful lookup status; long descriptions expand without
  choosing, and evidence controls stay clear of text and covers. Reviewer retry
  follows current capability (#1010).

- Preserve precise automatic refusal reasons across restarts and show them in
  Library, Evidence and Policy preview. Expanded preview results follow the
  policy choices in document flow. Clean rebuild required (#1081).

- Pause new Google Books requests during an observed shared burst window and
  retain its remaining wait through provider recovery. iTunes overflow remains
  safely bounded and terminal; no additional provider requests are implied (#1082).

- Clarify why conflicting full-work and prologue identities remain for owner
  review: opening ISBNs do not establish body completeness, and independent
  adjudication remains separate from observed application evidence (#1079).

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

- A sole confirmed ebook cover remains available for owner review when sibling
  acquisition times out. Automatic cover replacement distinguishes a known
  empty target from an unverified incumbent and replaces a confirmed incumbent
  only with a strict rank upgrade. Existing installs require a clean rebuild.
  (#999)

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
