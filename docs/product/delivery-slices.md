# Delivery Slices

Authorities: the GitHub Project and its milestones for what is planned, ready,
in progress and closed; the slice tracker issue for a slice's outcome and
ticket list; and
[functional slice delivery](../development/slice-delivery.md) for the branch,
issue and review mechanics. This document is the ordered plan and its planning
rules. It records open checkpoints only: the exit ledgers for checkpoints 0.1.0
through 0.12.0 are closed history, kept in git and in their closed milestones,
and are not restated here.

## Purpose

Mechanical Librarian is delivered through small end-to-end outcomes rather than
through completed horizontal work packages. WP0-WP9 remain historical
capability-ownership buckets, not current issue parents. A functional slice
crosses whichever capabilities are needed to produce one executable, reviewable
result.

The sequence below is the default implementation order. Each assigned row has
one slice tracker and one bounded delivery transaction; a row explicitly marked
as requiring promotion remains an outcome only until that promotion completes.
The transaction may use one `slice/...` branch and pull request or sequential
independently safe vertical increment branches and pull requests. The tracker
contains the final ticket list and may narrow the outcome before work starts.
Divide the outcome into suffixed slices such as `F05a` and `F05b` only when they
are genuinely distinct supported outcomes. Split a transaction into additional
pull requests only for distinct vertical increments or a required security or
privacy separation, never into horizontal layers or merely because review found
more work.

The tables preserve assigned and historical delivery records. A future outcome
outside them remains a concise deferred issue with no `F` ID; a stable
functional-slice designation is assigned only when that same issue is
explicitly promoted for delivery. Deferred outcomes do not acquire trackers,
constituents, milestones, or implementation design merely to reserve future
sequence.

The currently numbered delivery milestones are internal pre-release checkpoints,
not automatic release-publication commitments. Their exit criteria remain
authoritative, but completing a checkpoint does not itself create a tag, GitHub
Release, supported version, or published release artifact. The checkpoint and
version for the first published release remain undecided and require a separate
maintainer selection plus all applicable release gates and release-publication
gates.

## Focused work outside the slice sequence

Fixes, audit corrections, security work, documentation, tooling,
dependency-only changes, maintenance, and standalone tickets use focused
ticket branches and PRs. They may be native prerequisites for a
new-functionality slice, but they are never absorbed into that slice merely to
satisfy its checks. A slice may still introduce a dependency needed for its
observable outcome under the repository dependency policy.

Publication-readiness #43 remains a separate first-release gate. Any frontend
architecture decision needed for F16a is owned by #152 during promotion rather
than by the closed #192 ticket. The previously listed correction, governance,
and prerequisite tickets are completed or superseded history, not current work.

## Closed checkpoints

Checkpoints 0.1.0 through 0.12.0 are complete. Their outcome tables and exit
criteria are in the closed GitHub milestones of the same names and in this
file's git history; the last revision carrying them is recorded in the pull
request that removed them. A closed checkpoint's issue ranges are historical
context and never permission to reopen a legacy parent or constituent.

## Delivery checkpoint 0.15.1: owner-confirmed semantic cover decisions

| Outcome | Issue | Demonstrable result | Planning state |
| --- | --- | --- | --- |
| F21 Owner-confirmed cover decisions | #501 | One exact-current cover review shows the incumbent and safe report-bound alternatives, presents contained observation and deterministic policy only as advice, persists an explicit owner keep/select/no-action outcome and its successor history, and applies a selected original through the existing guarded Audiobookshelf path. | Activation candidate open for maintainer review. |

F21 reuses the 0.15.0 packet, identity, observer, policy, containment, and
versioned engineering benchmark evidence. Observation or recommendation
unavailability keeps identity-admitted manual selection fully supported.
Automatic model-selected writes, bulk reevaluation, silent learning, new
providers, release, tag, and deployment work are excluded.

## Delivery checkpoint 0.15.4: completed publication and recoverable advisory covers

This checkpoint composes the next bounded cross-layer capabilities without
granting a release, tag, deployment, or production operation.

| Slice | Tracker | Demonstrable outcome | Planning state |
| --- | --- | --- | --- |
| F22 Complete per-work publication | #597 | After every provider lane reaches a terminal outcome, one work publishes its complete bounded result; sibling failure stays inspectable without hiding coherent evidence. | In delivery as a one-issue activation slice; independent focused prerequisite #418 is complete. |
| F23 Qualified advisory cover review | #600 | Standard preparation automatically performs one bounded, qualified advisory cover review for the work. | Candidate complete: advice remains exact-current and owner-only; unavailable review retains manual comparison. |
| F24 Provider/configuration-scoped canary recovery | #408 | One durable provider- and configuration-scoped canary recovers safely within its bounded scope. | Activation candidate: the supervisor and owner CLI share exact canary/member claims; successful members complete #630's proposal, admission, projection, and atomic package cutover without rerunning healthy providers. |

#418 is an independent focused prerequisite to F22, not an F22 constituent.
#594, #595, #596, and #418 are focused defects with `Delivery slice: Not
applicable`. #598 and #599 are focused defects blocked on the final installed
#591 Activity baseline. Milestone 23 remains externally owned and is neither
copied nor modified by this checkpoint. This checkpoint creates no horizontal
plan or additional constituents.

## Delivery checkpoint 0.17.0: completed full-library experience

| Slice | Tracker | Demonstrable outcome | Planning state |
| --- | --- | --- | --- |
| F25 Bounded EPUB series evidence | #375 | Exact installed inspection and report paths retain explicit package, front, and rear series/sequence indicia without prose or catalogue inference. | Complete. |
| F26 Cold-start automatic decision tranche | #744 | One measured field tranche advances automatically during the qualified cold-start path without widening authority beyond its evidence. | Complete. |
| F27 Owner-managed settings | #763 | The owner manages typed configuration through one authenticated API and native Settings surface. | Complete through constituent #764. |
| F28 Guided browser first run | #745 | An owner progresses from an empty volume through configuration and preparation into the first usable Library handover. | Complete. |

## Delivery checkpoint 0.18.0: covers and responsiveness

This checkpoint makes cover choice useful without an AI reviewer, makes optional
reviewer setup an owner-managed product flow, and keeps full-library interaction
and preparation legible at realistic scale.

| Slice | Tracker | Demonstrable outcome | Planning state |
| --- | --- | --- | --- |
| F29 Reviewer-independent cover choice | #864 | An owner can inspect and act on a deterministic recommendation among confirmed cover matches without configuring an AI reviewer; qualified automatic policy remains explicit and owner decisions remain final. | Ready; the focused reliability prerequisites its reviewer-dependent qualification waited on are met. |
| F30 Explainable cover observations | #865 | Cover evidence distinguishes high-signal defects from owner preferences through closed, observation-only dimensions before any new automatic authority exists. | Ready; the focused resource prerequisite #866 is met. |
| F31 Owner-managed reviewer engines | #872 | First run and Settings let the owner continue without a reviewer or configure one usable engine per task, including contained Codex device authentication and OpenRouter cover observation through constituent #873. | Ready; the focused profile prerequisite #867 is met. #873 still follows F30. |
| F32 Pipelined preparation experience | #869 | One server-owned preparation run moves from the final setup step into a selectable summary above Works and a right-panel detail, while naming active books and showing how many made it through each stage; terminal dismissal, reconnect, compact, and reduced-motion behavior remain truthful. | Ready. |
| F33 Remembered owner session | #862 | One server-authoritative owner session remains valid across ordinary browser and application restarts for up to 30 days without becoming sliding or browser-stored authority. | Complete. |

The selected F29, F31, and F32 storyboards are directional visual references,
but their ticketed hierarchy, state meaning, interaction ownership, focus
continuity, responsive composition, and reduced-motion equivalence are protected
acceptance characteristics. `DESIGN.md` and direct exact-installed inspection
remain the visual authority; implementation variance cannot remove or substitute
an accepted state or interaction.

Focused work remains outside the slice sequence, each using `Delivery slice: Not
applicable`. #867 then #866 restored honest, repeatable reviewer operation; #853
bounded owner-felt read latency; #871 contained malformed provider inputs; #870
shapes provider work queries and is the one still open. F29 followed the
reviewer defects only where its qualification consumes reviewer evidence; its
reviewer-independent owner path remains the slice's required floor. F31 is one
delivery transaction with #873 as its independently closable constituent.

## Delivery checkpoint 0.19.0: evidence economy and decided fields

This checkpoint spends provider request budgets deliberately, widens keyless
first-run corroboration, and converts the measured classes of stalled owner
decisions into qualified automatic decisions.

| Slice | Tracker | Demonstrable outcome | Planning state |
| --- | --- | --- | --- |
| F34 Inventaire provider evidence | #883 | Inventaire serves qualified keyless edition-scope claims, covers, and identifier-first lookup through the existing provider route. | Ready. |
| F35 Consensus title election | #884 | A title with recognizable local-only noise advances automatically when every present provider converges on the clean form. | Ready. |
| F36 Language equivalence classes | #885 | Same-language representation variants collapse into one policy-rendered alternative; genuine conflicts survive. | Ready. |
| F37 Empty-description tiebreaker | #886 | An empty description fills automatically under a qualified deterministic tiebreaker. | Ready. |
| F38 Canonical contributor display | #887 | Canonical equivalence elects the display form as the stored value and the decision is stated plainly. | Ready. |
| F40 Series fill-empty decision | #895 | An empty series field fills automatically from one qualified work-scope authority. | Ready. |
| F41 Title decoration equivalence | #896 | Decorated title variants collapse to the plain canonical form, qualifying applicable tidy and subtitle tranches and the explicit author-prefix-plus-underscores WORK-identity compound without admitting either part alone; genuine conflicts survive. | Blocked by #884. |
| F42 Verified-manifestation ISBN decision | #897 | Competing edition ISBNs fill an empty field only when the already-verified manifestation identity decides them. | Ready. |
| F39 Advised description rewrite | #892 | The configured reviewer engine offers one rewritten or fused description as marked authored content; an explicit owner policy may apply in-policy rewrites automatically, and otherwise candidates park for owner review. | Ready. |

Focused work remains outside the slice sequence, each using `Delivery slice:
Not applicable`. #954 is complete; it restored the exact-head delivery gate
before this checkpoint. #908 is complete after this documentation checkpoint
merges.
#881 makes provider spending identifier-first and is Ready; #882 spends
first-run budgets on decisive works within #876's pacing and is Blocked by
#881. #888 is Ready to qualify one closed Latin combining-mark family for WORK
retrieval and admission; broader transliteration remains excluded. #889 is
Ready to admit reordered exact author sets in the WORK lane, measure-first;
election-side normalization remains excluded. #898 is Ready to stop creator
extraction admitting prose fragments as authorship claims, with an
extraction-only boundary and no corroboration election. #890 and #891 are
Ready to keep the fields rail and Library rail legible. #877 is Blocked by
every other open milestone issue as the closing measurement, grading 0.18.0
and 0.19.0 together against the #744 baselines (isolated 0.18.0 attribution is
deliberately traded away; per-issue evidence and the preserved 2026-08-25
volume carry the interim proof).

The tranche slices F35–F38, F40, and F42 use the #445 qualification route.
F41 uses that route for its applicable tidy and subtitle tranches. The
audiobook evidence-source selection (#893) stays outside this checkpoint.

## Delivery checkpoint 0.20.0: audiobook foundation

This checkpoint opens audiobooks as a read-only evidence surface: local
evidence first, provider discovery and enrichment after it, under the keyless
posture of [ADR-0061](../architecture/decisions/0061-audiobook-provider-posture.md).

| Slice | Tracker | Demonstrable outcome | Planning state |
| --- | --- | --- | --- |
| F43 Local audiobook evidence | #749 | Each single-audio-file item of a mounted audiobook library produces a closed local evidence set — ASIN, narrator, series and sequence, duration, embedded chapters, performance type, language, description, embedded-cover presence — with per-surface provenance, projected read-only to the owner. | In delivery. |
| F43 Audiobook provider evidence | #749 | The same item is compared against four keyless providers — Audible, Audnexus, AudioSilo, iTunes — configurable from Settings and the first-run wizard. An ASIN is adopted only where a second authority corroborates the recording's own running time, chapter structure, or perceptual cover hash; nothing corroborated leaves the item at `evidence_only`. Read-only throughout. | In delivery. |

## Delivery checkpoint 0.21.0: audiobook decisions

This checkpoint takes audiobooks from read-only evidence to decided fields:
multi-file items join the evidence surface, corroborated evidence becomes
proposals and owner decisions, qualified classes become automatic decisions,
and guarded application follows the existing consent and effect path. It is
shaped from the 0.20.0 production measurement recorded on #749.

| Slice | Tracker | Demonstrable outcome | Planning state |
| --- | --- | --- | --- |
| F44 Multi-file audiobook evidence | #965 | Every multi-file item of a mounted audiobook library produces the F43 closed evidence set with per-file provenance, composed duration and chapters, and cross-file consistency evidence, then enters the same provider path. | Delivered. |
| F45 Audiobook review cases | #966 | An audiobook with an admitted work identity produces proposals and, where policy leaves a choice, a review case; every prepared audiobook ends in a first-class completed state; decisions are recorded but not applied. | Blocked by #884 and #896. |
| F46 Qualified automatic audiobook decisions | #967 | The dominant audiobook decision classes earn automatic authority through the #445 route against an audiobook golden corpus and sealed holdout. | Blocked by #966, #895, and #964. |
| F47 Guarded audiobook application | #968 | Exact-current human and qualified automatic audiobook decisions apply through the existing guarded Audiobookshelf path, canary first. | Blocked by #966. |
| F08 Audiobook media-health observations | #144 | The audiobook report states typed, cited media-health observations distinct from metadata evidence. | Ready. |

Focused work remains outside the slice sequence, each using `Delivery slice:
Not applicable`. #956 feeds gate-promoted and target-asserted ASINs into the
per-ASIN lanes within one preparation pass, bounded and corroboration-gated,
and is the checkpoint's first unit. #940 is delivered inside F45's lane and
closes with it. #969 is Blocked by every other 0.21.0 issue as the closing
read-only measurement of the reference audiobook library against the 0.20.0
baselines.

No release, tag, deployment, chapter or cover or media write, transcription
(#24), ebook-provider audio dispatch (#962), or ebook format-ladder work
(#751 rungs 1–2) is included.

## Delivery checkpoint 1.0.0: guarded mutation and product operation

Write-capable slices use fake or disposable services until their explicit
canary slice. Destructive operations remain outside ordinary review and apply.
The original WP1-WP9 parents and their constituent forests are closed,
superseded planning history; they are not current delivery tickets and must not
be reopened or recreated.

| Outcome | Issue | Demonstrable result | Planning state |
| --- | --- | --- | --- |
| F08 Audiobook health report | #144 | One generated audiobook produces bounded tag, chapter, duration, and media-health observations without transcription or production mutation. | Retained planned designation. |

The checkpoint's other outcomes are delivered and their trackers are closed:
F15a supported container operation #151, F16a authenticated browser review and
decision #152 (through #447, #448, #449, #450 and the milestone-13 corrections
to #455), F17a continuous review orchestration #473, F13 guarded fake-service
apply #149, F14 Audiobookshelf canary apply #150, and F17 continuous pre-release
operation #153. Cross-case history remains #195.

The outcomes are not one serial chain. Readiness records the real dependencies:
#70 supplies explainable proposals; F10b admits eligible proposals; F16a records
applicable current human decisions and qualifies the first-release browser
baseline; F13 supplies the common human and qualified-automatic guarded
fake-service path; F14 follows completed F13 qualification for its disposable
Audiobookshelf canary; F11 is terminal historical context with only its
deterministic packet and decision projection retained; F15a supplies the
supported operating surface; and F17 composes the useful guarded path under
repeated operation without depending on future target CAS. F08 remains an
independent evidence outcome. A retained `F` ID is a planned designation on an
assigned outcome issue, not proof of `Ready` status or authority to begin.

### 1.0.0 checkpoint exit

- every write is current, authorized, backed up, idempotent, verified, audited,
  and recoverable;
- read-only discovery and diagnosis continue when writes are disabled;
- unattended report-only continuation resumes from durable scheduler state
  without widening its initiating principal, authorization context, scope,
  versions, bounds, expiry, or mutation authority; each transition actor is
  recorded against that authority, and process IDs or detached watchers remain
  diagnostics only;
- the normal deployment is one supported container with one persistent volume,
  read-only media mounts, and one HTTP port;
- one authenticated owner can inspect a current case and make one persistent,
  server-authoritative individual decision through the F16a first-release
  responsive, keyboard/focus, accessibility, private caching/storage,
  content-security, hostile-rendering, performance, synthetic-artifact, and
  primary-supported-browser baseline; and
- the complete useful path passes bounded continuous-operation qualification
  against exact-current state without adding release or compatibility promises.

Satisfying this checkpoint exit does not itself publish a release. First-release
artifact production #94 and publication-readiness audit #43 are separate gates
in the `first-release` milestone and require an explicit maintainer publication
decision. Post-release migration and older-schema restore recovery remain #57;
neither is F17 acceptance.

## Deferred outcomes

The current deferred product outcomes are:

- opening-audio evidence #24; its former #104–#110 decomposition is closed
  superseded history;
- one outcome per further metadata provider: LibraryThing #211, Project
  Gutenberg #212, LibriVox #213, and Audible #214;
  the old F09c/F09d/F09e trackers #205, #206, and #208 are closed superseded;
- bulk browser review and extended qualification #195 after the first public
  release;
- Calibre-Web Automated library-provider support #316 after the first public
  release;
- optional Audiobookshelf notifications and polling enhancement #65;
- post-release migration and restore recovery #57; and
- the [additional reviewer backends](future-reviewer-backends.md), embedded-file
  updates, plugin loading, and other optional extensions.

These outcomes stay concise and have no functional-slice designation, milestone,
or pre-created constituents unless explicitly selected. Open Library #210 is
no longer deferred: it is the completed F09l slice in milestone 0.8.0 with no
constituents. Closed speculative trackers, WP parents, and constituent forests
remain historical inputs and never become current work automatically.

## Planning rules

- A named `Deliver slice <ID>` instruction selects that assigned, unblocked
  `Ready` tracker. Without a named ID, the highest-priority assigned, unblocked
  `Ready` tracker determines execution order across work packages. Neither form
  selects or promotes an `Unassigned backlog` outcome.
- The Project `Delivery slice` field records each new-functionality ticket's
  primary slice. Focused work uses `Not applicable`.
- Slice trackers carry their exact closure mapping. Historical issue ranges in
  completed checkpoint records are context, not permission to reopen or close a
  legacy parent or constituent.
- A deliberately narrow adapter or interface is acceptable when it completely
  demonstrates the slice and preserves compatibility for later extension.
- No slice may defer its required safety, recovery, observability,
  documentation, clean-schema handling (or post-release migration), or test
  obligations to a later slice.
- Historical work-package parents remain closed; a current slice neither
  reopens them nor inherits their superseded constituent forest.
