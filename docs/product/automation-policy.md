# Automation Policy

Mechanical Librarian is designed to make useful, non-destructive decisions without routine human approval. [Decision quality and human attention](../../PRODUCT.md#decision-quality-and-human-attention) defines the product's balance between useful metadata, residual uncertainty, and owner effort.

This document defines product intent. Exact thresholds remain versioned configuration and must be qualified through shadow runs before they authorize production writes.

## Operating posture

The current title qualification is
[F19a v34](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v34.json) (#1074).
The ISBN-and-creator title option introduced by
[F19a v15](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f19a-qualification-manifest-v15.json) (#1007) is measured separately: twenty-five synthetic
positive opportunities and two independently labelled retained real positives
reach correct candidates and durable Automatic decisions; twenty-five synthetic
negative controls are withheld. All fifty-two cases remain owner-reachable.
The original nineteen synthetic positives, fifteen negatives and two real
positives remain covered; six positive and ten negative controls cover populated
targets, complete ordered creators and repeated opening observations.
One retained truth comparison treats straight and curly apostrophes as
equivalent without changing the provider title. F35's 50 and F41's 75 historical
constructor controls retain their expected decisions through current consumers.
These finite checks make no population accuracy or full-library savings claim.
Historical qualification manifests retain their original option sets. Published
v11 remains readable but cannot grant current authority; pre-option manifests
still reject the new option. The current companion F20 continuity boundary is
documented in [rich-metadata qualification](rich-metadata-qualification.md).

The initial production profile is **decisive non-destructive**:

- make field-level decisions instead of accepting or rejecting a whole item;
- use embedded file and audio evidence as first-class evidence;
- accept reasonable evidence when credible sources agree and no material contradiction exists;
- automatically reject no-ops, clearly worse candidates, and inapplicable fields;
- send only genuinely unresolved choices to the active human queue;
- keep automatic approvals and rejections visible in history; and
- never automate deletion or another destructive operation.

The reference deployment may accept a measurable correction or override rate in exchange for a much smaller queue. Public defaults must expose this tradeoff plainly and allow a user to begin in shadow mode.

## Accepted title and ISBN direction

This product decision was accepted on 2026-09-11 and is implemented and qualified
by #1007 through the current title branch described above. It replaces this document's earlier intended requirement to
extract or preserve subtitle and series/order components from the local title
before accepting a provider-backed title. It does not retroactively change
historical qualification results. Current runtime authority comes from the
explicit qualification envelope and architectural contracts delivered by #1007.

- Automatically prefer a useful existing provider title when the retained
  evidence convincingly connects it to the same work and there is no material
  competing evidence. One reliable provider can suffice; a provider-count
  majority or mandatory second provider is not the definition of sufficiency.
- A valid extracted ISBN linked to the work is strong title evidence. Multiple
  print/ebook ISBNs that converge on the same work and title need not block a
  work-level decision because their editions differ. A single ISBN family is
  not a universal requirement. Preserve source provenance and distinguish an
  ISBN belonging to this work from an unrelated occurrence or contradictory
  provider association; checksum validity alone does not establish that link.
- File, package, and target-service metadata are observations that may need
  correction. Their title disagreement alone must not permanently veto
  stronger applicable evidence. Independent local content agreement adds
  support, but extracting a matching local title is not mandatory when the
  provider/ISBN evidence is sufficient.
- Providers may supply subtitle and series metadata through their own field
  decisions. Do not require parsing those components out of the old local
  title, reconstructing them as sibling proposals, or settling uncertain
  series positions before making an independently supported title decision.
  Retain original observations in provenance. Apply edition-specific evidence
  requirements only to fields or changes for which the edition matters.
- Escalate material competing works, authors, titles, or other consequential
  ambiguity. Several returned records sharing a title do not by themselves
  prove work identity. Failed, unavailable, or missing sources supply neither
  corroboration nor dissent. Preserve owner-final decisions and Ask me/Ignore;
  the goal is to reduce unnecessary choices under Automatic.

Qualification should target realistic positives and material counterexamples,
including the effect on owner attention. It must not turn an aspiration toward
high accuracy into a requirement for zero bibliographic error or exhaustive
proof before a bounded useful improvement. Exact software contracts and write
safety still require their applicable checks.

## Current decision-only qualification boundary

The 0.12 automatic posture is limited to the sealed F19a qualification
envelope. It records immutable, origin-labelled automatic decisions only; it
does not apply a plan or write target metadata, media, artifacts, or a
production service. Preview remains non-authoritative. Owner pause, global or
category suspension, and explicit recovery fail closed by retaining would-be
automatic work in human Attention. Any field, option, or policy revision outside
that exact envelope remains human-routed until separately qualified.

An audiobook decision class earns automatic authority only through its own F46
record. An identity is `category:option:audiobook_reason`: the owner's existing
category and option, plus the exact audiobook shape a record qualified. No
ebook envelope admits one and an audiobook record admits no ebook option, so an
option qualified for ebooks never becomes automatic on a recording because it
shares a category name
([ADR-0073](../architecture/decisions/0073-audiobook-decision-qualification.md)).

While no record names an identity the projection abstains for the whole
recording, exactly as it did before F46, and the decision the owner records
applies through the guarded path
([ADR-0069](../architecture/decisions/0069-audiobook-review-cases-and-terminal-outcomes.md),
[ADR-0071](../architecture/decisions/0071-book-media-guarded-application.md)).
Once a record exists, Automatic is withdrawn from every destination on that
recording the record does not name, so an unqualified identity is always the
owner's answer to give and never an authority failure.

The [F46 qualification record](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f46-audiobook-qualification-candidate-v8.json)
names each measured field/operation that may become Automatic. It preserves the
previous 44 operations and adds the accepted audiobook metadata remainder
(#1047). Useful candidates remain available independently of their automatic
eligibility. These bounded development examples support no population-accuracy
or library-wide coverage claim.

Work-only audiobook review (#1166) supports explicit owner choices for description
and genres, and (#1253) for series, series position and a witnessed title
cleanup. Each source needs its own complete work witness; recording identity
remains unresolved. Automatic becomes Ask and Ignore remains Ignore. A
description or genre choice whose alternatives only trim or extend the current
value stays choosable without being queued unless its policy explicitly asks.
No subtitle, other field, cover, authored-description, or automatic authority
follows from this scope. Manual choices still require existing consent, currentness and verified
supported-service application. The following qualified rules apply within the
existing recording-admitted route.

One applicable independent source can suffice. A complete title/author match in
a successful canonical ASIN lookup, or a bound independent local ASIN, can
support work facts even when recording identity remains unresolved. Runtime alone
and a target lookup hint cannot establish work identity. Complete author and
series associations survive; malformed credits never authorize partial lists.
One exact terminal ` (Unabridged)` may be ignored in local audiobook matching
when the remaining full title and complete authors agree. This is not a title
rewrite, provider-title stripping, or inferred abridgement authority.
When the provider's running time is within 90 seconds of the recording's own,
the asserted title's one series decoration (a parenthesized series name or an
inverted series prefix such as "Saga Book 2 - Title") may also be ignored when
the recording's own series states that name and position, and
dotted initials such as "J.R.R." match their spaced form (#1260). The runtime
never matches on its own, and dramatisations, translations and other variants
still do not match. A recording admitted this way follows the ordinary
recording route, so its title change stays an owner choice when the series
the decoration named is not preserved.

Recording facts retain field-specific relationships and runtime/variant vetoes.
Explicit language, abridgement, full-cast and part conflicts matter; unknown
language does not invent a conflict. Bounded complete chapter correspondence may
support a recording without treating media-health diagnostics as identity votes.
Narrator agreement can also qualify when every plausible source member supplies
the same complete cast. A missing or malformed cast cannot be omitted from that
agreement, and narrator agreement grants no identifier authority. A narrator
replacement whose cast shares nobody with the recording's own file credits
reaches the owner, because it points at another recording.
An author or narrator candidate that only drops or moves the accents of a
current member, on either medium, keeps that member's current spelling, so an
otherwise equal cast is a preservation and a cast that also adds someone is an
Add; a candidate that adds accents is still a replacement. A contributor
candidate any of whose members is a credit line ("Narrated by …", "Read by",
"Narrator: …") is never automatic and stays the provider's exact text for the
owner to choose; a credit-line incumbent is still replaced by a plain cast
(#1283).

Title and author preservation records one durable no-change, removes the
decision from pending application work, and calls no target or effect guards.
A recording's authors in another order are such a preservation, never an Add. Supported normalization
and Add remain separate operations. Existing fill-only choices preserve
populated values. Series Add retains current associations; complete-set
replacement requires its separate choice and refuses conflicting sequence facts.
Language comparison recognizes equivalent ISO codes and the observed English
display name without discarding region or script. Both language options retain
the admitted source string; neither renders a new display name. `Ask me` exposes
Language in remaining fields whenever its existing owner actions admit a choice;
recording that choice retires its attention. Unknown-only evidence and an
equivalent current value do not create a choice, and Ignore remains excluded.

Audiobook subtitles may fill an empty value or replace a recognized whole-field
award, bestseller or adaptation announcement with supported descriptive text.
A title candidate on either medium with any part (between a colon, a spaced
dash, a comma, a semicolon or an enclosure) that is such an announcement is
never automatic and remains the owner's choice; a current title holding one may
still be replaced by a plain title (#1283).
Descriptive collisions and materially different subtitles remain visible. A
corroborated title/subtitle split requires both options Automatic and both
current decisions and consents. One PATCH applies the pair, both read-backs are
required, and uncertain or partial results use the existing reconciliation path.

Synopsis prefers a safe nonempty summary over a short description. Only an absent
or empty summary permits fallback; malformed, unsafe, oversized or cutoff text
does not. Differing regional synopses of a well-matched audiobook work do not
require routine review. The existing preferred source authority supplies the
text; equally preferred source records use stable provider/record identity order,
without claiming one is more authoritative. Other descriptions remain available.
Text length, wording, opaque alternative hashes and provider vote counts do not
rank them. Unsafe relevant sources and competing text within one source record
still refuse automatic selection; ebook description rules are unchanged.
A synopsis that is only a structured credit line (narrator, reader, ripper or
tagger credits, optionally with format words, a length, a publisher or a release
date), a format word such as "Unabridged" or a cut form of it, a numbered
chapter, track, part, section, disc, CD or side marker, an encoder watermark,
markup with no text, or `n/a`, `none`, `unknown` or `tbd` describes nothing:
under "Replace safe plain text" such an incumbent is replaceable, and such a
candidate never fills automatically (#1250). A genre list holding only format
words such as "Audiobook" counts as empty for an audiobook, so "Fill when empty"
may fill it and a format-only candidate is no candidate and never makes a real
genre list ambiguous; adding a genre beside a format-only list is recorded as a
fill (`genres:add:audiobook_genres_fill`); a list naming any real genre is kept.
Ebook genres are unchanged.
On both media, a description candidate that is mostly attributed praise -- quoted
spans with their attribution holding at least half its non-whitespace
characters, one attributed by a dash -- or that opens with "Praise for" is never
automatic or recommended, and stays choosable. A praise incumbent is not a
placeholder, so it is preserved (#1283).
Compatible complete explicit tag lists may form a source-bound union
for fill/Add, preserving owner members and all contributing references. Tags
remain off by default. Tag replacement retains its separate election and never
uses genres as tags.

Audiobook publication means original work publication. Year, month and day
precision survive both date options, PATCH and subsequent reads. Recording
release, copyright and edition dates do not vote; ebook manifestation meaning
is unchanged. Publisher/imprint comparisons respect their roles and retain real
conflicts.

ASIN and ISBN operations require the appropriate recording association and valid
canonical values. A well-matched audiobook ISBN need not have a separate format
label, but is not publisher-verified. Competing products, other-format claims,
and insufficient association remain inspectable. Activity distinguishes absent,
unavailable, malformed/incomplete, conflicting and insufficiently associated
source values without overriding earlier owner, policy or currentness refusals. Evidence also shows non-voting audiobook identifier observations with their source record,
reference, scope, state and applicability. These are labelled external observations,
not corroboration or eligible choices; malformed or absent values are stated explicitly.

Cover replacement requires a known incumbent with a shorter edge below 300 pixels
and an eligible portrait/square candidate of at least 500 pixels and twice that
incumbent edge. Adequate, unknown or stale incumbents are preserved. Source rank,
aspect ratio or a tiny size increase alone is insufficient. The controlled
positive proves the effect path, not natural replacement coverage.

The optional adult-product rule may set `explicit` to true from supported positive
source evidence. False or absent evidence preserves the current true, false or
empty value; it never clears or certifies clean content. Application remains
separately default-off. Adding explicit write support lapses earlier consent
envelopes; owners must grant a new exact subset before any application.

F35 (#884) adds a closed, provider-consensus title election to the v14
ProposalSet policy: one exact local noisy title, one exact reversible pattern,
byte-identical clean work or edition title observations from at least two
independent authority correlations, and no dissent. Colon subtitles corroborate
only across sources and elect only when their exact suffix is safely retained
as the current structured subtitle or an independent provider subtitle
candidate. Bracket query shaping is deliberately broader than election
authority: only the exact `[retail]` source suffix is in the current closed
election lexicon, and any other bracketed suffix abstains. #445's successor
manifest replays and diffs every existing F19a option alongside the title
tranche; its 1.0 precision and zero safety failures authorize only this closed
F35 title replacement. The sealed prior F19a digest remains readable historical
authority for its former options; it cannot authorize F35. F20 continues to
exclude title. A colon segment stripped from any WORK-scope title claim, local
or provider, corroborates the F20 `subtitle:fill_empty` projection on
cross-source terms only: it never elects a subtitle, never corroborates one
stated by its own source, and on the election seam only names the guard that
closed the choice.

F41 (#896) collapses title alternatives that are equal up to one closed
decoration into a single equivalence class whose canonical member is the plain
form. Both members must be provider-backed; the class needs exactly two
alternatives; the decoration must match in exactly one grammar step, so stacked
decorations stay competing, and the step is exact, so removing the decoration
must yield the plain alternative byte for byte. Two evidence-only guards close
it further, both comparing under the closed title comparison: every valid
provider WORK title claim in the report must key into the class, and at least as
many distinct provider authorities must attest the plain form as the decorated
one. When the current value is the plain form the outcome is a keep with zero
writes; when it is the decorated form the plain alternative is proposed through
the qualified automatic `title:replace_target:decoration_tidy` option, bound to
the F19a successor manifest v5 and lane `title_layout`. The F20 candidate
advances to v10 with the subtitle option re-scored on segment corroboration,
which is withheld when the segment is generic, when any provider states a
different subtitle, and when no provider authority carries the segment or the
authorities carrying the decorated form—the local title counted—exceed the
provider authorities stating the plain form.

F38 (#887) separately qualifies only
`contributor_names:canonical:provider_consensus_display`: two independent
present provider authorities must agree on one exact contributor display, the
local equivalent display must be the sole dissent, and the original local claim
remains immutable provenance. The persisted decision binds the F38
aggregate-safe manifest, while contributor-name suspension remains the safety
lane for this reason-specific authority. Ordinary `contributor_names:canonical`
continues to bind its historical F19a manifest and gains no F38 authority.

The published F20 rich-metadata candidate qualifies the three `series` options,
both `isbn_form` options, genre fill/add/replace, tag fill/add/replace, safe
plaintext description, and locally corroborated subtitle fill for decision-only
Automatic. Subjective fields qualify on exact work-bound source projection,
completeness, and safe operation—not on matching one preferred taxonomy or
prose answer. `subtitle:fill_empty` fills an empty subtitle only when the
book's own packaged metadata attests the exact provider value; uncorroborated
candidates stay owner decisions. When Subtitle routes to Automatic, the
qualified fill owns the subtitle destination unless an acting title-combination
candidate already covers it; a manual title-combination witness is replaced by
the fill, and a later human title split proceeds through the existing override
and revoke path. Safe plaintext fills an empty Description from one exact
target-work-applicable source. Competing descriptions first pass the fixed
80-character, 16-word, no-visible-cutoff floor and then the fixed Hardcover,
Google Books, Open Library authority order; provider text is never combined and
an unavailable provider does not change the order. Corroborated split and
Google visible-text remain `not_qualified`; ASIN remains
`insufficient_coverage`; `tags:off` is policy-only and stays the default; and
`explicit:unsupported` is human-only. Owners retain `Ask me` or `Ignore` for
every non-automatic rich option. This authority records immutable decisions
only and does not bypass effect consent or any guarded-write control.

Qualified series fill (#895) fills empty Automatic targets only and
preserves existing memberships. One provider can supply the required WORK
backing; independent corroboration is not a quorum. Absent numbers remain
absent. Malformed selected evidence and failures of contributing dispatches
abstain; unrelated providers, candidates and query lanes cannot veto a choice.
A unique existing source bundle may resolve compatible subset alternatives,
including in Ask. It retains its exact order, values and provenance, never a
cross-source union. Closed leading-article and trailing `Series`/`Trilogy`
variants use the existing name grammar; known positions must agree exactly or
as ordinary unsigned decimals. Ambiguous maxima, disjoint bundles and known
position conflicts remain reviewable. No fuzzy matching, provider ranking or
number inference is available.

Selected series sources must also describe the target work. The
[series-only ISBN identity rule](../architecture/decisions/0070-isbn-backed-series-identity.md)
permits complete ordered author comparison, an exact retained edition title,
or a one-step parenthesized series name explicitly attested by that same
candidate. It does not admit unrelated works, general subtitle stripping or
WORK-query transformations. All candidates in the successful target-backed
ISBN lane must qualify; Ask and non-series applicability stay unchanged.

The [F20 v17 candidate](https://github.com/atabbott/mechanical-librarian/blob/608854ea0de96b71931e0c2338d97312dc8f469e/docs/reference/f20-rich-metadata-qualification-candidate-v17.json)
rotates current decision authority after binding the corrected cover packet
producer and live attention currentness. Its complete 35-source audit preserves
every qualified non-cover outcome, value, reason, source binding and correctness
result. Earlier manifests, including byte-identical v16, remain immutable
historical evidence; their decisions are readable but cannot authorize new writes. The
[qualification record](rich-metadata-qualification.md) states the source-label
corrections, reduced coverage, distinct-book denominators and limits of the evidence.

## Availability before curation

### Deterministic cover choice

Cover recommendation is independent of the optional reviewer. Only confirmed
same-edition and same-book candidates participate. Same edition ranks first;
then portrait aspect sanity, pixel area, width, height, canonical packet/source
precedence and an opaque stable reference form one total order. Observations can
explain visual traits but cannot admit, exclude, rank, delay or authorize a
cover.

Automatic readiness requires at least one confirmed candidate and no exact
provider/report lane actively fetching. An owner may explicitly confirm an intact,
current retained alternative with an unconfirmed match or lower visual quality.
Positive identity contradictions remain excluded, including retained creator
disagreement classified as unknown. Owner confirmation does not upgrade evidence. Retry/backoff waiting is quiescent. Automatic is default-off and
available only as an explicit opt-in after every frozen cover-election cohort
passed its public aggregate-safe manifest with zero false admission or
ordering/readiness disagreement. Standard consent, backup, currentness,
recovery and read-back safeguards remain mandatory. A strictly better later
candidate may supersede an automatic choice; an owner choice ends that automatic
authority. A fresh explicit owner confirmation may revise the current choice
before a cover effect is claimed; claimed, applying, reconciling and verified
cover effects cannot be revised.

Ingestion and consumption do not wait for metadata curation.

1. The target-service adapter (Audiobookshelf first) discovers and exposes the
   new item using whatever metadata is initially available.
2. Mechanical Librarian observes the item after a configurable stability delay.
3. Evidence, review, and safe apply proceed asynchronously.
4. Failures leave the item available with its prior metadata and create an operational or review state as appropriate.

The curator must not quarantine an otherwise usable item merely because metadata is poor or evidence services are unavailable.

## Evidence-role and sufficiency contract

The planned policy keeps five roles separate: target-service adapters observe
and mutate supported service state; local extractors produce bounded content
evidence; optional metadata providers produce remote evidence; deterministic
domain policy decides sufficiency, candidates, and disposition; and isolated AI
reviewers recommend among supplied proposals. No role may infer the
configuration, capability, outcome, credentials, or authority of another.

The current snapshot is authoritative for the exact scoped target state used by
no-op detection, optimistic concurrency, apply planning, and verification. Its
metadata remains one versioned bibliographic observation, not privileged truth.

Each policy version includes a sufficiency matrix by field, media type,
operation, and change class. Fill-empty, replace-populated, no-op, rejection,
and edition-sensitive work are distinct entries. Each entry declares:

- when normalized current and local evidence can support deterministic closure
  or a bounded proposal without an external provider;
- when materially independent provider evidence is required for a disposition;
- which missing-provider conditions route to human review; and
- which capability combinations produce an explicit unsupported outcome.

Providerless report-only operation is valid. Inventory, local evidence,
deterministic no-op and rejection handling, bounded proposals, and human review
continue when all external metadata providers are disabled or unavailable. The
sufficiency matrix prevents this from silently weakening fields that require
provider evidence.

## Initial field policy

| Field or action | Intended automatic behavior | Escalate when |
| --- | --- | --- |
| Title | Fill or correct from convincing same-work evidence under the [accepted title and ISBN direction](#accepted-title-and-isbn-direction). Provider subtitle/series decisions do not require extraction from the old title. | Material competing work, author, or title evidence, or consequential edition-sensitive ambiguity. |
| Author or authors | Fill, normalize, reorder (ebooks only; a recording's reorder is a preservation), or correct when identity evidence supports the people and roles. A controlled package creator placeholder remains visible evidence but cannot qualify an authors proposal by itself. | Authorship conflicts, contributor roles cannot be mapped safely, or a collective/pseudonym choice is materially ambiguous. |
| Series name | Fill or correct when the work-to-series relationship is supported. | Sources disagree about whether the work belongs to the series or represent different series. |
| Series index | Fill or correct when numeric, decimal, or labelled order is supported. | Reading order and publication order conflict without a selected policy, or the evidence is internally inconsistent. |
| Publication date | Fill or correct from applicable embedded edition evidence; use catalogue evidence when edition applicability is credible. | Only work-level dates are available for an edition-specific field or sources materially conflict. |
| Publisher | Fill or correct from applicable embedded edition evidence or a credible edition match. | Imprint and publisher roles are ambiguous or the evidence appears to refer to another edition. |
| Identifiers | Add valid, typed identifiers and replace malformed or invalid current values under ordinary applicable field evidence. Supersede a validated current comparable identifier only when an applicable embedded/content identifier and a materially independent provider identifier agree on the same validated canonical replacement identity. Preserve raw observations and lineage. | Conflict identifies another applicable edition, or required validation, edition applicability, materially independent lineage, or agreement on the same canonical replacement identity is missing or unresolved. |
| Cover | Replace when the candidate is clearly more legible, relevant, and visually useful. Exact regional edition matching is not required. | Candidates are similarly good but materially different, work identity is uncertain, or the image is misleading for the content. |
| Description, genres, tags, narrator, and other fields | Preserve initially unless a field-specific policy and adapter capability have passed qualification. Qualified subjective-field operations project one exact work-bound source value without synthesis or semantic relabelling; tags remain off by default. | Provenance is incomplete, sources would need synthesis, the operation is unqualified, or evidence does not support field applicability. |
| Duplicate or media removal | Never automatic. | Always requires a separate destructive review and explicit human authorization. |
| Embedded media-file write | Disabled in the first production phase. | Requires a later feature with independent file backup, validation, and rollback policy. |

The publication row describes intended field policy. The
[current publication candidate rule](../operations/proposal-set.md#publication-candidate-sufficiency)
requires local evidence plus two independent provider correlations at the
selected precision. Candidate availability, Ask-mode owner-action reachability,
and qualified automatic authority are separate; #1003 retains the current rule
pending publication-specific usefulness evidence.

## Current options by media

This table reports the existing ebook F19A/F20/cover and audiobook F46
qualification summaries. It does not grant write permission or promise that a
particular work has sufficient evidence. A missing summary means unavailable,
never qualification inherited from the other media kind. “Fill” requires a
known-empty usable target; “preserve” records no target change. Other operations
remain subject to their exact evidence and currentness envelope.

| Policy field | Option | Ebook operation scope | Audiobook qualified operations |
| --- | --- | --- | --- |
| `title_layout` | `preserve` | fill or replace | title: fill; title: preserve; title: replace |
| `title_layout` | `title_and_subtitle` | fill or replace | title: fill; title: preserve; title: replace |
| `date_precision` | `year` | fill or replace | No automatic effect |
| `date_precision` | `full_date` | fill or replace | publishedDate: fill |
| `language_rendering` | `preserve` | fill or replace | language: fill; language: normalize; language: preserve; language: replace |
| `language_rendering` | `display_name` | fill or replace | language: fill; language: normalize; language: preserve; language: replace |
| `contributor_names` | `preserve` | fill or replace | authors: add; authors: fill; authors: normalize; authors: preserve; authors: replace |
| `contributor_names` | `canonical` | fill or replace | authors: add; authors: fill; authors: normalize; authors: preserve; authors: replace; narrators: add; narrators: fill; narrators: normalize; narrators: preserve; narrators: replace |
| `publisher_preference` | `publisher` | fill or replace | publisher: fill; publisher: preserve |
| `publisher_preference` | `imprint` | No automatic effect | No automatic effect |
| `subtitle` | `fill_empty` | fill | subtitle: fill |
| `subtitle` | `corroborated_split` | No automatic effect | subtitle: fill |
| `subtitle` | `replace_descriptive` | No automatic effect | subtitle: preserve; subtitle: replace |
| `series` | `primary_narrative` | fill | series: fill; series: preserve |
| `series` | `source_order` | fill | series: fill; series: preserve |
| `series` | `all_qualified` | fill | series: fill; series: preserve |
| `series` | `add_qualified` | No automatic effect | series: add; series: fill; series: preserve |
| `series` | `replace_qualified` | No automatic effect | series: fill; series: normalize; series: preserve; series: replace |
| `genres` | `fill_empty` | fill | genres: fill (an empty or format-only list, #1250); genres: preserve |
| `genres` | `add` | add | genres: add; genres: fill; genres: preserve |
| `genres` | `replace` | replace | genres: add; genres: fill; genres: normalize; genres: preserve; genres: replace |
| `tags` | `off` | No automatic effect | No automatic effect |
| `tags` | `fill_empty` | fill | tags: fill |
| `tags` | `add` | add | tags: add |
| `tags` | `replace` | replace | No automatic effect |
| `description_fill` | `safe_plain_text` | fill | description: fill; description: preserve |
| `description_fill` | `google_visible_text` | No automatic effect | No automatic effect |
| `description_fill` | `replace_safe_plain_text` | No automatic effect | description: fill; description: preserve; description: replace |
| `isbn_form` | `preserve_exact` | fill | isbn: fill; isbn: preserve |
| `isbn_form` | `prefer_isbn13` | fill | isbn: fill; isbn: normalize; isbn: replace |
| `cover_order` | `fixed_source_order` | fill or replace | No automatic effect |
| `cover_order` | `replace_if_strictly_better` | fill | cover: replace |
| `asin` | `preserve_exact` | No automatic effect | asin: fill |
| `asin` | `ebook_manifestation` | No automatic effect | No automatic effect |
| `explicit` | `unsupported` | No automatic effect | No automatic effect |
| `explicit` | `adult_product` | No automatic effect | explicit: fill; explicit: preserve; explicit: replace |

The ebook `replace_if_strictly_better` cover option fills known-empty covers only;
it does not replace a confirmed incumbent. `fixed_source_order` retains its
existing strict-rank upgrade. Unavailable, ambiguous and owner-final cover
states still abstain. Audiobook replacement remains the existing bounded
small-incumbent/material-improvement operation.

### Audiobook identity chapter

Title and author operations require recording/work evidence under the existing
F46 identities. Narrator changes require `contributor_names:canonical`;
choosing Preserve does not qualify narrator writes.

Two qualified replacements narrow per case and grant nothing (#1252). A title
replacement that deletes a series label (text on either side of the kept
title, set apart by a separator or an enclosure, in which the series grammar
reads a label or one of the case's own series names appears) asks the owner
unless the series is already populated, the same outcome set fills it
automatically, or the same outcome set's automatic subtitle split carries the
removed segment. A narrator
replacement asks the owner when the file's own narrator credits and the
product's cast share no member under closed display equivalence; a file tag
crediting several narrators is never split, so any product name inside it is
shared. Subtitle fill and supported
promotional-subtitle replacement have separate qualifications.

### Audiobook publication chapter

Full-date publication fills qualified original-work dates. Year-only has no
automatic effect. Publisher fills but imprint does not. ASIN fills only under
`preserve_exact`; ebook-manifestation selection does not authorize an audiobook
write. ISBN and series operations follow the exact rows above.

### Audiobook curation chapter

Tags replacement and Google-visible description text have no automatic effect.
Qualified description operations preserve useful prose or use the separately
bounded replacement rule. Adult-product explicit values require the current
qualified boolean evidence. Cover authority remains separate from ebook cover
admission. Ask me and Ignore, owner-final choices, consent, backup and verified
read-back remain authoritative for every chapter.

### Reading a policy preview

The preview captures active policy, library generation/resolutions and at most
20 ordered current report/proposal pairs in one SQLite read snapshot. Both
active and draft are evaluated after that read ends, using the same retained
inputs and values. This is a captured database view, not a promise that the
remote service or a later request is unchanged. Activation still checks the
captured active-policy identity and generation. A later return to the same policy
identity does not make an older preview current. Only a retry of the exact next
generation transition may return its prior result.

Primary counts describe sampled works and fields whose selected outcome or
meaningful candidate routing changed because of the edit. A supported
Ask-to-Automatic candidate counts even when it equals the incumbent. Changes to
manual description, genre or series availability for work-only audiobooks also
count, even when no single value is elected. Unchanged
outcomes and projected changes against library values are separate context;
proposal identities and explanatory wording alone are not impact. Title,
authors, narrators, date, publisher, language and cover are outside this rich
projection and explicitly labelled not previewed. Preview and policy activation
grant no write permission.

## Evidence posture

Evidence is evaluated for applicability, independence, and contradiction—not only by a single numerical confidence score.

Agreement is meaningful only when the agreeing observations are materially
independent. Several adapters repeating one upstream catalogue record count as
correlated support, not several independent confirmations. Policy records this
relationship before source agreement can increase automatic-write eligibility.

Preferred evidence includes:

- embedded EPUB package metadata and bounded front matter;
- valid identifiers mined from the file;
- audio container tags, chapters, and duration;
- qualified bounded spoken-credit claims when the optional
  [opening-audio evidence](opening-audio-evidence.md) extension is enabled;
- the target service's current state;
- catalogue records that can be tied to the same work or edition; and
- corroborating cross-format evidence for the same owned item.

Current service metadata is one sourced observation with its own provenance and
version. It is not privileged truth: policy compares it with applicable local
and provider evidence while preserving it for explanation, stale-plan checks,
and rollback.

EPUB package and opening-matter facts are first-class evidence records, not
merely hints supplied to a catalogue or reviewer. Applicable local facts may
establish an edition or support a field proposal without a remote catalogue
match. Package metadata and front matter from the same file can corroborate
internally but retain one correlated file lineage.

Ambiguity risk facts include generic or very short titles, initial-only or
collapsed author names, narrator/author role confusion, swapped title and
author fields, conflicting series orders, and release-name debris. These facts
raise the evidence required for replacement but do not turn a hard-coded list
or fuzzy-match score into authority. The report's closed NFC/case/whitespace
and typographic forms, plus conservative single-author comma inversion, may
explain agreement only when complete ordered creator identity and role remain
intact. Structurally bound aliases may support identity while preserving every
full observed and proposed name; unbound aliases remain neutral.
An untyped creator role remains neutral. It can corroborate an otherwise
complete explicit-author set only across two distinct provider authorities for
the same qualified work, with a complete ordered identity-bound creator set
and exact identity (or, across provider-native namespaces, closed
normalized-name) agreement. It is never a
provider-neutral conversion from an untyped creator to an author.

An embedded value is not automatically true, and a catalogue value is not automatically edition-applicable. However, imperfect evidence can still support a non-destructive improvement when the competing explanation is materially less plausible.

Provider outage, timeout, quota exhaustion, authentication failure, and empty malformed response are operational facts. They are never evidence that a candidate or field is absent.

Candidate sourcing is accounted for by a versioned manifest derived per lane
role. Inventory lanes require target capability and scope. Local-evidence lanes
use target, media, field, and extractor capabilities. Provider-evidence lanes
use target, media, field, provider, query, and configuration capabilities.
Candidate lanes depend on relevant successfully completed evidence and the
active policy. A dimension irrelevant to a lane is absent, not a failed
capability, and derivation never branches on service or provider names.
For every
derived lane it records applicability. Applicable lanes record a typed execution
outcome. Only successfully completed applicable lanes record candidate count,
which may be zero, and execution mode `executed` or `reused`; `reused` is valid
only for the exact target scope, matching input fingerprint, compatible
provider, query, configuration, and artifact-contract versions, and a validated
complete expected artifact. A missing derived lane is a coverage failure.
Failed execution is never reusable; failed, stale, partial, or incompatible
execution follows governed retry, backoff, and suppression and cannot be
silently skipped.

Provider configuration, declared capability, connection availability,
execution outcome, candidate count, and evidence assessment are orthogonal.
Unconfigured and unsupported do not mean failed; unavailable, degraded, failed,
or empty are typed scoped outcomes; and none is negative metadata evidence or
independent agreement. Provider lanes cannot block unrelated current/local
evidence or another provider lane.

The application constructs a minimal bounded allowlisted `ProviderQuery` for an
applicable provider lane. The adapter receives only that query, its own scoped
credential, and its restricted network client. It never receives a raw
`ItemSnapshot` or service row, target endpoint or credential, unrestricted
external ID or path, media handle, database repository, or mutation port.

### Directional title arbitration

The [accepted title and ISBN direction](#accepted-title-and-isbn-direction)
governs intended title replacement. Recognized cleanup may remain useful, but
recognizing the local title's shape is not a prerequisite for an evidence-backed
replacement. Shorter text is not inherently better, and adding recognized noise
is not an equivalent improvement. Materially competing bibliographic evidence
remains an exception; merely differing local/provider title strings need not be.
The current ISBN-linked title branch implements this accepted direction under
its qualification envelope; earlier title branches and their historical
preservation rules remain intact.

### Candidate aggregation

When equal normalized candidate values are combined, every source assessment,
lineage, applicability fact, and contradiction remains individually auditable.
The policy engine recomputes the aggregate disposition deterministically from
the current assessments. It neither unions all historical adverse facts nor
allows a strongest-row winner to erase a genuine conflict.

Candidates retain generic source-adapter or provider provenance and correlation,
but provider name is not semantic proposal identity. For the same canonical
target, snapshot, field, normalized value, proposal schema, and policy, equal
values collapse while retaining all distinct evidence lineage.

### Identifier arbitration

Identifier ingestion preserves the raw value and declared source type before
validation. Policy validates syntax, canonicalizes recognized aliases and
comparable ISBN forms, and compares values only within compatible namespaces.
Non-comparable identifier types are neutral: neither agreement nor conflict can
be inferred from them. Source lineage and correlation remain attached through
canonicalization.

One distinct checksum-valid ISBN with typed format- and edition-applicable
content provenance can strongly establish an edition only when a materially
independent, format-appropriate provider returns the same canonical ISBN,
including ISBN-10/ISBN-13 equivalence, and the target, work, and media identity
agree. Multiple distinct content ISBNs, unrelated preview or other-work claims,
correlated providers, and edition, format, narrator, abridgement, dramatization,
omnibus, or cross-media conflicts remain unresolved. A spoken ISBN is useful
typed audio evidence but is not equivalent to an embedded/content ISBN for this
rule.

A malformed or invalid current identifier may be replaced under ordinary
applicable field evidence. A validated current comparable identifier may be
classified as stale and superseded only when an applicable embedded or content
identifier and a materially independent provider identifier agree on the same
validated canonical replacement identity. Otherwise the conflict remains
unresolved or the candidate is rejected according to field policy. Resolving an
identifier, title, or any other field cannot erase or implicitly decide sibling
field proposals.

### Series-label reconciliation and projection

A provider series label may be canonicalized only by a qualified, versioned
mapping applicable to the supported work identity and source contract. The
evidence record retains the raw, normalized, and canonical labels, source
lineage, work identity, mapping-rule version, and replay or supersession
relationship.
Unknown or out-of-scope labels remain unchanged and visible; production-learned
author-, title-, or library-specific aliases are not silently hard-coded into
policy.

Shared evidence may be projected to another confirmed target, but a source AI
recommendation travels only as provenance. It is not evidence, independent
agreement, target disposition, human decision, or apply authority. The
destination independently revalidates target and work identity, edition or
field applicability, current state, evidence lineage, contradictions, and the
current policy version before creating or resolving a proposal.

## Supervised report-only continuation

Unattended continuation is allowed only for report-only work already authorized
for a bounded run. Its durable authority fixes the initiating principal and
authorization context, target and operation scope, configuration, policy and
schema versions, resource and time bounds, cancellation behavior, and expiry
before scheduling begins. Every actual transition actor is recorded and linked
to that authority reference. Resume may recover or complete that exact work; it
cannot widen scope, enable apply or destruction, change skills or policy, or
replace the governing authority record.

The application scheduler owns continuation through durable jobs, leases,
fencing, and validated outcomes. Operating-system PIDs, detached watchers,
shell sessions, and tool sessions are diagnostic implementation details only.

## Cover policy

Cover selection optimizes for usefulness rather than exact edition purity:

- correct work identity is mandatory;
- readable title and relevant artwork matter more than regional imprint;
- UK and US variants are ordinarily interchangeable;
- resolution, aspect ratio, crop, artefacts, and text legibility are quality facts;
- a known illustrated/non-illustrated mismatch is a negative relevance fact, not an automatic rejection when no better cover exists; and
- an existing acceptable cover remains when no candidate is clearly better.

A cover change keeps the previous cover as rollback evidence before the target API is called.

## Queue policy

The active queue contains only work that can benefit from a human choice:

- two or more materially plausible candidates;
- evidence contradictions that policy cannot resolve;
- unsupported but potentially actionable target capability;
- high-impact non-destructive changes outside the qualified policy; or
- destructive actions requiring explicit authorization.

Operational failures belong in health and job views, not the metadata decision queue. Automatic rejections are closed by default but remain searchable for audit and quality sampling.

Deterministic title cleanup and degradation outcomes also remain outside the
active queue. AI `manual` is reserved for materially plausible supported
alternatives, not for policy-resolved normalization direction.

## Qualification and monitoring

Before a field policy becomes write-capable:

1. Run it in report-only mode on synthetic and representative fixtures.
2. Run a bounded shadow cohort against real items without applying changes.
3. Review a sample of approvals, rejections, and manual outcomes.
4. Record false-positive, false-negative, correction, override, unresolved, and verification-failure rates.
5. Enable a small canary with strict batch and circuit-breaker limits.
6. Expand only when failures are understood and rollback evidence is complete.

Quality monitoring continues after enablement. A material rise in human overrides, stale plans, verification mismatches, or correlated errors automatically disables the affected policy category while read-only processing continues.

## Policy versioning

Every decision records:

- policy profile and version;
- evidence and candidate versions;
- deterministic rules applied;
- reviewer backend, model/profile, prompt, and schema versions when AI is used;
- resulting disposition; and
- later human override or rollback, if any.

Changing applicability rules, automatic-write eligibility, evidence weighting, or material prompts creates a new policy version and evaluation cohort.

## Supported unchanged titles

Ordinary title preservation with positive bound evidence records one Automatic
no-change decision even when no changing bibliographic combination exists. Ask
and Ignore remain owner policy, not safety failures. No-change bypasses target
application guards because it creates no write or plan. Actual malformed
Automatic authority still suspends its category (#1073).

Language names/codes are grouped before election for both media (#1071).
`English`, `en` and `eng` can support one alternative; undetermined `UND`/`und`
is readable evidence, never a choice or preservation vote. Equivalent populated
values stay unchanged. When filling, Preserve prefers an observed code and
Display name prefers an observed English name, falling back only to another
form in the elected evidence. Base/script disagreements and unsupported forms
remain manual; no language is inferred or synthesized.


## Genre work applicability (#1074)

Automatic genre fill accepts existing closed creator display equivalence,
including a singleton surname-comma-given inversion, as absence of contradiction.
This does not establish positive work identity or broaden provider admission.
Unavailable, failed and no-match providers cast no dissenting genre vote;
genuine competing alternatives abstain and populated genres remain fill-empty
preservation. Explicit Ask exposes existing eligible owner actions, while Ignore
creates no automatic decision. Owner-final choices supersede automatic authority.
The existing consent, currentness, backup, read-back and audit guards still apply.


## Same-name title and series (#1075)

Whole normalized-token equality permits only a supported series-name slot to
coexist with a preserved KEEP title while filling empty series. It grants no
title replacement, sequence exemption or series-identity merge. Existing
association, title-witness, policy, owner-final, consent, backup, currentness,
read-back and audit checks remain mandatory. Proposal policy v31 requires a
clean rebuild; report v49 remains unchanged. No new egress or write scope exists.

## Recorded automatic refusals (#1081)

Final evaluations retain field-specific refusal codes. Missing eligible evidence,
contradicted work applicability, unproved association, missing edition evidence,
unusable current values and unavailable contributing sources remain distinct.
The compiler's first applicable refusal governs; ISBN checks retain malformed or
incomplete evidence, conflicting families, insufficient association, unavailable
source, then absent-value precedence. A failed unrelated lookup does not veto a
supported value. No new option, disposition or effect permission is introduced.

Library and Evidence read the immutable final evaluation without recompiling its
report. A missing record is described as unavailable recorded evaluation; a legacy
generic reason says that no more specific reason was recorded. Neither implies
missing evidence or conflict. Current automatic suspension is a separate overlay
from the same snapshot, labelled separately from the recorded evaluation. Owner
choices and Ask/Ignore retain precedence. Explicit draft previews and selected-value
effect validation still compile retained evidence through their existing guards.

Outcome v10: **clean rebuild required** for prior derived stores;
there is no adoption or migration. F19a, F20 and F46 semantic revision 4 preserves
the existing permission sets. Earlier documents remain historical decoding only.

## Material ISBN/title conflict precedence (#1132)

An explicit ISBN/title material conflict remains manual even when a supported
candidate equals the current title. Automatic records a conflict without
suspending title automation; Ask and Ignore retain their policy behavior.
Ordinary title preservation, independently supported replacements and explicit
owner choices retain their existing authority and effect guards. Shadow policy
v19 and compact successor qualification bindings require a clean rebuild.


A sole eligible scalar title that exactly matches the incumbent uses the compiled
preservation action only when both actions carry identical citations and neither
belongs to a combination. It records one unchanged result without an effect or
owner attention. Material conflicts and Ask/Ignore remain prior constraints (#1138).
