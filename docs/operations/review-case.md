# Review-case admission

The `AdmitReviewCase` application operation admits one exact current ProposalSet
during authenticated review preparation. A typed source-only inspector is
available to contributors against disposable state; admission and inspection
are not installed commands.

The typed canonical result is `admitted`, `replayed`, `superseded`, `no_case`,
`retired`, or `stale`. `propose_change`, `acceptable_choice`, and
`unresolved_choice` results create a case; `no_case` and `retired` have no
empty case. Untrusted failures are redacted as fixed `invalid`,
`incompatible`, `currentness`, or `persistence` codes.
`superseded` and `retired` require an active prior case. After a `no_case` or
`retired` checkpoint, the next actionable set is `admitted` and another
nonactionable set is `no_case`.
An unavailable external currentness preflight is `currentness`; repository
read or write failure remains `persistence`.

The complete `review-case-v2` case binds the exact ProposalSet, report, target
and file identity. It also projects the exact
`bibliographic-relationships-v1` document. Preserve selections are rechecked
against the bound target field, so a changed value or changed missing/null/empty
state changes case identity and cannot replay under an old combination.
Per-destination review proposal IDs bind the report, target, and exact result
set independently of the relationship projection. Adding or removing a
relationship tuple therefore changes ProposalSet, case, packet, and request
identity without renaming an otherwise unchanged field proposal. A rejected
tuple makes all three title/subtitle/series proposal projections explicitly
manual, including exact target-bound companions whose raw result has no
invented alternative.
Inside the admission transaction, the service recomputes the fixed F09o
evaluator output from the bound report; a synthetic or otherwise different
ProposalSet, including any changed or omitted compatible-combination tuple, is
`incompatible` and publishes no review state. A report or media
pointer that loses currentness returns `stale`.
A case names the media its proposals were formed for. An audiobook case is
admitted, decided, and read exactly as an ebook's, and the decision it records
carries `application='apply'` on either medium: it plans and applies through
the one guarded path
([ADR-0071](../architecture/decisions/0071-book-media-guarded-application.md)).
When only the book is supported, the same Library/detail view explains that the
recording is unresolved and offers supported series, title, description and
genre choices only; the subtitle is never offered. Sources retain their
provenance; Keep and Accept use the existing owner workflow. A description or
genre choice that only trims or extends the current value stays choosable
without counting as needing attention, unless its policy explicitly asks. That
holds only when the current value passes the audiobook synopsis and genre
floor: a placeholder, a text holding markup, a visibly cut-off text ending
"..." or "--", or a format-only genre list keeps every alternative material,
so the choice counts as usual.
Automatic, other fields, covers and authored descriptions gain no authority from
this work-only scope. Consent, stale-source/target refusal, verified application
and Activity history remain the existing guarded path (#1166, #1253).

Audiobook Automatic requires the exact F46 field, option and actual operation
identity. Unqualified candidates remain owner decisions. A fill record cannot
authorize replacement: creation, reopening and guarded effect reconstruction
bind the desired value to the recorded pre-value and qualification digest.
Ask, Ignore, owner-final and pause retain their existing authority. A
`recorded_only` document retained from before audiobook writes existed is
readable history on an audiobook case only: it is stale against an ebook case,
is refused as a new command, is excluded from both pending-write queries, is
never planned by the continuous scheduler, is refused by name by the planner,
and the history says so where the owner reads it
([ADR-0069](../architecture/decisions/0069-audiobook-review-cases-and-terminal-outcomes.md)).

Lineage checkpoints retain an attributable fixed-system audit transition. A
newer report makes an earlier case historical without rewriting it. Use a
clean rebuild and regenerate supported reports/ProposalSets for an older
database; admission itself performs no decision, apply, or write, and no
migration command exists.

Inspection returns the immutable case and derived currentness without writing
it. Both operations validate the complete canonical typed result. Earlier
schemas are rejected under the clean-state policy.

## Field classification

Library, Attention, and case detail project one closed field classification.
`needs_attention` and `manual_fallback` are the only Attention membership and
ordinary-next states. `automatic_recorded` remains inspectable and editable but
does not count as owner work; an automatic `abstention` is instead a manual
fallback. `human_recorded` is retained inspection, and `unsupported` explains
the unavailable capability without inventing a selectable empty value.
`owner_choosable` is a recording's settled fill that does not queue, such as a
tag or ASIN fill, which never queues on its own, or narrators outside "Authors
and narrators: Ask me": it stays editable as an optional choice and is neither
queued nor counted (#1249). SQLite uses the same membership expression for the
list and both counts; application projection supplies the owner-readable
reason and never promotes an invalid row into an Attention failure.

## Authenticated exact-current inspection

With an exact-current application database and explicit `review` configuration,
the supported serving boundary uses the configured browser transport:

```sh
mechanical-librarian review-serve --config config.json \
  --database reports.sqlite --catalogue catalogue.sqlite \
  --artifacts artifacts --host 127.0.0.1 --port 8443
```

The configured `canonical_origin` must name that exact external browser origin,
including a non-default port. For direct HTTPS configure both `tls_certificate_file` and
`tls_private_key_file` as absolute paths to one matching identity, and make
the certificate owner-controlled and the private key owner-only regular files
(`chmod 644` and `chmod 600`, respectively). Missing, linked, writable-by-other,
unreadable, encrypted, malformed, or mismatched inputs fail noninteractively
before the database or network listener opens. Private-key passphrases are not
a supported configuration or environment input; empty-passphrase encryption
is still encrypted and is rejected by the PEM-envelope boundary. There is no
automatic transport fallback. Explicit HTTP and HTTPS proxy modes omit both TLS paths; see
[deployment](deployment.md) for the three closed modes. Forwarded proxy identity is not accepted by Uvicorn;
application proxy trust remains an explicit,
bounded allow-list and should normally stay empty for direct TLS.
The direct edge permits at most 100 live HTTP connections, closes incomplete
request headers after 10 seconds, caps an incomplete H11 event at 16 KiB, and
uses only ECDHE AEAD cipher suites for TLS 1.2 (with the runtime's TLS 1.3
suites). Rotate certificate files by replacing them and restarting the process;
an active process continues serving its validated anonymous snapshot.

For an isolated development qualification only, a synthetic one-day identity
can be created and then referenced from the two configuration fields:

```sh
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout review-test.key -out review-test.crt -days 1 \
  -subj '/CN=127.0.0.1' -addext 'subjectAltName=IP:127.0.0.1'
chmod 644 review-test.crt
chmod 600 review-test.key
curl --cacert review-test.crt https://127.0.0.1:8443/health/live
```

This synthetic command is not production certificate management. Operators
remain responsible for obtaining, rotating, and securely mounting a certificate
whose names cover the canonical origin; the application neither issues nor
renews certificates. The container's own `HEALTHCHECK` works this way, trusting
the mounted certificate as its anchor for direct HTTPS. HTTP and proxy backends
use credential-free loopback HTTP readiness.

`/health/live` and `/health/ready` are the only anonymous operational data.
Both are exempt from origin checking, neither takes a credential, and between
them they return `ok`, `ready` or `draining` and nothing drawn from the
library; [observability and support](observability.md) defines what each one
means. The login surface verifies the owner-only Argon2id hash file, applies a
durable bounded throttle, and creates an opaque server-side session whose token
is stored only as a digest.
All review responses, except hashed review assets and cover previews (#1193),
are private/no-store, and all enforce the canonical Host
and Origin, restrictive CSP, HttpOnly SameSite cookies (Secure for browser HTTPS), CSRF on logout
and decisions, and explicit proxy trust. The `/api/v1` surface exposes
exact-current case detail, bounded evidence, current-case admission and decision
history, one individual field-decision operation, and authenticated OpenAPI.
The mutation requires the displayed proposal ID and target fingerprint, an
explicit `accept`, `keep_current`, `reject`, `owner_entered` or `withdraw`
outcome, a 16–128 character `Idempotency-Key`, the session CSRF token, an exact
proposed value for `accept`, and the owner's value for `owner_entered` (#1285).
Same-key/same-payload retries return the original decision identity;
conflicts and stale state fail without a partial decision or event. The response
also carries `confirmation`: either `confirmed` with the actual case detail or
`unavailable` if its post-commit readback failed. An unavailable confirmation is
still a recorded decision. The browser keeps it unconfirmed and offers an explicit
current-case reload; it never submits an automatic replacement decision. A same-key
retry may obtain a new confirmation without creating another decision. Recording
and readback share one foreground local-operation lifetime; background activity
cannot enter between them.
The generated OpenAPI marks the owner session cookie and required mutation
headers explicitly. Decision attempts for absent case IDs return `404`, while
invalid sessions return `401`, CSRF mismatches return `403`, and persistence
outages return `503`. Logout clears the cookie only after revocation succeeds.
The Policy notebook uses this same authenticated boundary. It initializes a
separate draft from the read-only built-in active policy; validate, immutable
create, read, and preview use deterministic versioned responses. After exact
preview, the owner may activate that immutable draft. One transaction persists
the exact resolved per-library documents, retires every current unapplied
old-policy pointer together with its current review package, fixes the
transition total, and changes the active generation. An item that then fails
reconciliation therefore leaves no old-policy case current to block a later
preview or activation. Successors return progressively through the existing pipeline from
eligible persisted evidence. Ask retains human work; Ignore removes that
category from actionable attention with a typed policy result. Automatic stays
unavailable until qualified and Explicit is unsupported.

The compact transition status is server-authoritative and survives restart.
`completed_with_failures` retains typed diagnostics and explicit retry.
Retry restarts each failed item whose fixed evidence is still current and
reopens its preparation scan so the work is claimed; an item whose work has
moved to a newer report is resolved as superseded, stored as complete with its
`stale` code and shown to the owner as reconciled, because that report prepares
under the current policy; an item with no preparation work stays failed.
Acknowledgement dismisses only that transition notification. New work uses the
current policy immediately without changing the fixed cutover total. Cutover
adds no provider reacquisition, second queue, decision transfer, automatic
authority, apply plan, target write, or media write. The policy-only script is
served separately from the shared Library and Attention interaction bundle.
While processing, the notebook refreshes this compact status at a bounded
interval; retry restarts polling, while terminal or acknowledged state stops it.
Another activation cannot overlap processing. It supersedes a completed or
`completed_with_failures` transition the owner has not dismissed, acknowledging
it in the same transaction while its items and counts stay as recorded. An exact response-loss retry returns the original transition.

The owner-facing notebook groups the closed policy catalogue into Identity,
Publication, Curation, and Unsupported content chapters. Its sticky desktop
index becomes a pinned chapter selector at compact widths. Field names,
representation choices, and Automatic, Ask me, or Ignore posture are presented
in user language; a disabled Automatic choice explains whether the field or
representation is unqualified or Preview mode has not been entered. The active
profile and changed draft remain distinct throughout validation, impact preview,
activation review, currentness conflict, and cutover progress. Impact uses
readable field and library names rather than policy identifiers. These display
groupings do not add categories, qualification, or authority. Activation review
contains keyboard focus until Cancel or Escape returns it to the review trigger;
the same closed draft, preview, and guarded activation operations remain
authoritative.
Authenticated `/library` provides bounded attention-first, all, and
no-attention-needed lenses; `/attention` contains only exact unresolved/manual
fields and links to the exact field on detail. Both support bounded search,
maximum 50-item pages, and query-bound cursors. Automatic-only cases remain
inspectable in the library but never gain a human control. Decision persistence
cannot create automatic authority, construct an apply plan, construct a target
client, perform a write, reverse a decision, or widen one exact ordinary field
into a bulk operation. Each detail, evidence, current-case history, Library, or
Attention response is assembled from one database snapshot, including its
decision summaries and counts. A concurrent supersession or decision is thus
visible either throughout the response or not at all; snapshot failure returns
the existing value-free unavailable response rather than partial data.

The Library surface also provides one explicit **Scan now** action per
configured library. It persistently and progressively refreshes that library's
catalogue entries, generates exact-current reports, creates deterministic
ProposalSets, admits ReviewCases, and projects them into the existing Library
and Attention views. Duplicate requests coalesce, claimed work resumes after a
restart, and two fixed lanes allow another ready work to advance while one work
is slow. Compact checked/total/ready/blocked progress is authenticated,
private/no-store, and contains no metadata values or paths. Confirmed absences
and present items that are no longer eligible are staged after complete
enumeration; they tombstone or retire current lineages only if the whole scan
completes successfully and a confirmation discovery still observes the same
absence or ineligibility. A stale or absent catalogue establishes its baseline
progressively, one successfully observed library at a time, so an unavailable
library cannot block healthy libraries; later library scans retain unrelated
entries. The two lanes serialize catalogue publication and share synchronized
provider pacing. Every configured provider lane records a bounded terminal
observation. A failed, unavailable, ambiguous, disabled, or no-match lane
remains visible without erasing independently coherent sibling evidence or
stopping proposal and projection. Failure to construct or validate a complete
report remains a bounded failed work for a later owner-requested scan.
If an item disappears after discovery, its typed not-found result requests the
same bounded rediscovery as other target or media drift, including between
proposal creation and admission. A failed rediscovery terminalizes the scan;
its old queued children cannot execute. Authentication and service failures
remain distinct technical failures.
If another file appears while extraction is active, the final progressive-only
singular-work gate likewise publishes no report or case and requests bounded
rediscovery. Slow live currentness reads do not hold the SQLite writer lock
needed by the other lane.
When preparation changes ready/blocked counts or lifecycle state, the Library
page refreshes its exact-current projection so newly admitted or retired cases
are reflected without a manual reload.

Preparation stages are deliberately not Library publication. The service
publishes the report, proposal, case (or no-case disposition), and projection
as one current package only after projection succeeds. During a refresh the
last complete package remains browseable; during a first generation nothing is
listed. Operators recover failed work with the existing bounded retry/scan
controls rather than editing SQLite pointers.

The supported container requests every configured library on first startup and
after `review.library_polling_interval_seconds` from its last successful scan.
Repeated automatic and owner requests coalesce. The compact shared activity
control appears only while active, paused, or actionable; pause prevents new
admissions without cancelling committed work and disables retry until resumed.
It states each library's scan state in owner words rather than the store's own
identifier, states checked, ready and could-not-finish counts for that library,
and lists every stage bucket in one fixed order while a scan is running so a
live update reads as progress; a finished scan lists only the buckets books
reached. **Scan now**, and **Retry** where a run reported failures, both start a
new preparation cycle for one library, and the control says so, including that
provider lookups which hit limits recover automatically on their documented
schedule and that a failure before a report has no automatic retry
([provider recovery](provider-recovery.md)). `Escape` closes the control and
returns focus to the button that opened it, and tabbing off either end closes it
where it stands rather than leaving it open behind the focus ring; a control
failure keeps its announcement while activity refreshes behind it.
Each activity response reads every configured library and the pause flag from
one validated repository snapshot rather than reopening the database per row.
Provider adapters perform their bounded local attempts and retain a safe
aggregate request count in the report. A terminal lane never opens a
whole-library circuit or delays unrelated work. Library exposes each
non-success lane's provider, kind, terminal status, bounded provider-request
count, and safe reason. A work that fails before a coherent package exists is
Library-only, never Attention, and carries bounded recovery guidance. Explicit
retry is offered after correcting a credential, configuration, or preparation
failure.
It makes only the reads and provider calls
already authorized by catalogue and metadata-report, and adds no field
decision, apply plan, target write, media write, deletion, or destructive
authority.

The login request body is capped at 2 KiB at the ASGI boundary before JSON
parsing. Five reserved attempts within five minutes close the login gate; the
reservation is recorded before Argon2 work so concurrent requests cannot
outpace the bound. A successful owner login clears the failure window.

## Cover considerations

For a current cover field, Detail presents the incumbent first and up to 12
safe report-bound alternatives. Advisory observation and deterministic policy
never select on the owner's behalf. The owner must explicitly keep the current
cover, choose one available original, or take no action. If observation is
unconfigured or fails, retained manual choices remain usable;
an explicit advisory retry creates a successor record and leaves the cover
unchanged.

Setup and Settings recommend GPT-6 Luna (`openai/gpt-6-luna`) for advisory
observations. The development comparison covered 39 works, 111 image appearances
and 96 distinct images; Luna returned 39/39 valid reports with 13 objective errors
across 1,060 assessed fields. This is not independent gold or error-free
qualification, and no explicit text-edition positive was tested. Inspect the
covers: accepted residual errors grant no automatic selection or write authority.
MiMo 2.6 Flash (`xiaomi/mimo-v2.6-flash`) remains a comparator available through
custom Model ID entry. Historical model labels remain unchanged.

Preparing or retrying a cover comparison is an explicit authenticated action.
Case, evidence, and history reads project one persisted snapshot and never run
the observer, policy, or a persistence command as a side effect.

Every consideration, supersession, owner outcome, decision, apply attempt, and
verification remains immutable history. History reopens each bounded historical
candidate set, admitted observation or unavailable state, deterministic policy
and recommendation, preferences, supersession/currentness, and associated owner
decision without making old image transport or write authority current again.
A selection enters the existing
Activity consent and guarded application path. It is not applied unless the
current case, consideration and cover policy, target and incumbent cover,
selected original digest, capability, backup, consent, and readback gates all
still pass. There is no bulk or model-selected cover application.

The installed `cover-review` CLI exposes the same server-authoritative current
consideration and bounded historical evidence used by the API and React view;
its explicit keep, select, and no-action commands record through the same
application service. `show` includes `current_cover_selection_id` from the same
current detail snapshot as its consideration. Deciding commands accept
`--expected-selection-id ID`; omitting it explicitly expects no current selection.
For a fresh revision, supply the shown selection ID and a new idempotency key.
An unconfirmed match requires `select --confirm-unverified-match`; the CLI never
infers that confirmation. Retry an uncertain command with its original key,
selection baseline, artifact and confirmation flag, rather than silently using a
new baseline. Existing provider recovery state is read-only and active cover
acquisition refuses a choice; absent recovery state is not created.
Changing reviewer configuration makes older comparisons stale. Prepare the current
comparison before confirming; continuing without a reviewer still permits eligible
owner choices. The decision captures current reviewer authority before its write
transaction, under the same command gate as Settings changes.

`review-activity` projects the same bounded guarded plan,
verification, stale, failure, and recovery ledger as authenticated Activity and
is read-only. It requires only the configured application database and the
configuration whose review limits shape that projection. It does not compose a
catalogue, artifact store, target writer, worker, or recovery writer, and it
does not accept paths that could imply those authorities. Capability display
uses only persisted suspension evidence and otherwise reports `not_ready`; the
command neither reads service credentials nor probes Audiobookshelf.

Preparation retains the exact committed successor before report readback or
notification. If settlement cannot commit, **Preparation stopped** preserves the
last durable progress and keeps published Library books available. Restart the
Mechanical Librarian service to resume its retained checkpoints; browser refresh
and Scan cannot clear the runtime stop. The authenticated incident is runtime
evidence, not a durable terminal work row. See [observability](observability.md)
and [the recovery decision](../architecture/decisions/0078-preparation-claim-and-crash-recovery.md).

## Recovering title decisions

In Policy, expand Safety and recover the title suspension. If automatic decisions
remain paused or globally suspended, completed work stays unchanged until the
last blocking control is explicitly cleared. Eligible completed
books are queued for re-evaluation of retained evidence; Library history records
the decision and Activity records a supported unchanged title as no change.
Library preparation shows “Title recovery requested” and the queued or running
re-evaluation, retaining unrelated work and earlier completion counts. Recovery
creates a visible run if the earlier one was dismissed.
Recovery does not apply metadata. Pause, global suspensions and other category
suspensions still apply. Books with owner decisions, stale evidence, Ask/Ignore
policy or work already in progress are not requeued. A book you hold is
re-evaluated, and its new title stays held until you resume automatic handling. An empty eligible
cohort is a successful recovery with no new work. A failed request retains the
suspension and work; inspect current state and retry explicitly.


### Policy comparison response

The versioned preview response retains `cases` and adds parallel `comparisons`
for each sampled case. Each comparison names `media_kind` and carries active and
draft field projections, their destinations, and `changed`. Both projections
come from one captured retained database snapshot; `changed` excludes ID-only
rebinding and reason-only differences. Current unapplied counts include work
outside the global 20-case sample; truncation is explicit. The ebook
`rich_qualification` response covers every policy option and includes
`operation_scope`; the separate audiobook summary retains its exact qualified
operations. Missing entries are unavailable, not a fallback qualification.
These read models confer no consent or effect authority. The reproducible
[OpenAPI document](../reference/review-openapi-v1.json) is the exact wire contract.

### Owner-confirmed retained cover choice (#1078)

Choosing a retained alternative opens a confirmation with its whole image.
“Match not confirmed” remains visible for unconfirmed evidence after recording.
Cancel or Escape before submission records nothing and restores card focus.
A lower-quality or unconfirmed alternative may be chosen explicitly; positive
identity contradictions, unavailable artifacts and active searches remain refused.
An admitted keep-current action stays available when all alternatives are ineligible;
keeping a cover retains its observed match status in history. The uncertainty
confirmation flag is accepted only for selecting an alternative.
A recorded choice leaves Attention but remains revisable from Library while no
cover effect has been claimed, applied, reconciled or verified. Each revision
compares the exact target selection token and appends history. A stale or uncertain
response requires a current reload; it never submits another choice automatically.
Recording is separate from consent, guarded application and verified read-back.
