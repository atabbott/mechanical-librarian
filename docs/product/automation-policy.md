# Automation policy

This document says how Mechanical Librarian decides each metadata field: when it
decides on its own, when it asks the owner, and when it leaves a field alone.
[Decision quality and human attention](../../PRODUCT.md#decision-quality-and-human-attention)
sets the standard it applies. The [review experience](review-experience.md)
describes how the owner sees and changes these decisions.

## Principles

The product is **decisive and non-destructive**:

- It decides each field separately. It never accepts or rejects a whole item.
- Embedded file and audio evidence is first-class evidence.
- Reasonable evidence is enough when credible sources agree and nothing
  materially contradicts them. Bibliographic perfection is not required.
- No-ops, clearly worse candidates and inapplicable fields are rejected
  automatically.
- Only genuinely unresolved choices reach the owner's Attention queue.
- Automatic decisions stay visible in History and Activity.
- Deletion, duplicate removal and other destructive operations are never
  automatic; they need a separate destructive review and explicit owner
  authorization. Embedded media-file writes are not supported ([scope](scope.md)).
- Curation never blocks reading or listening. Poor metadata, unavailable
  providers or a failed step leave the item available with its prior metadata.

The tolerance is for bibliographic uncertainty only; every safety and authority
check still applies
([PRODUCT.md](../../PRODUCT.md#decision-quality-and-human-attention)).

## Policy: Automatic, Ask me and Ignore

Each policy field has an **option** (how a value should look or be chosen) and a
**destination**:

- **Automatic** records a decision without the owner, but only when that exact
  option is qualified for that media kind (see [Qualification](#qualification)).
  Policy refuses to activate Automatic for an unqualified option; the Policy
  page shows it as unavailable.
- **Ask me** offers every eligible choice to the owner. The field joins Attention
  only when there is a real choice to make.
- **Ignore** leaves the field unchanged. Its proposals are marked
  `policy_ignored`; it creates no Attention row and no automatic decision.
- **Unsupported** is fixed for Explicit under its `unsupported` option.

An Automatic decision is decision-only
([ADR-0036](../architecture/decisions/0036-qualified-decision-only-automatic-policy.md)).
Writing it to the library is a separate guarded effect that needs the library's
write permission, current source and target state, a backup and read-back
verification
([ADR-0037](../architecture/decisions/0037-origin-neutral-guarded-field-effects.md)).
An owner decision always supersedes automatic authority for that field.

### Built-in policy

Each field's default option is marked **(default)** in
[options and defaults by media](#options-and-defaults-by-media). Every default
destination is Automatic except `tags` and `asin` (Ask me) and `explicit`
(Unsupported). A library may override any field. Selecting a manual-only
option while the field is Automatic visibly changes it to Ask me.

### Posture and safety controls

The global posture is Review, Preview or Automatic. Automatic decisions are
recorded only in the Automatic posture. Preview records what policy would do
without deciding, so an owner can start there and confirm Automatic later.

Automatic fails closed. While the owner has paused automation, or a global or
category suspension is active, would-be automatic work stays in Attention for
the owner. If recording automatic decisions fails for one category, that
category is suspended (`automatic_admission_failed`) and other categories carry
on. Any other authority failure suspends Automatic globally
(`automatic_authority_failed`). The owner recovers a suspension in Policy;
recovery applies nothing by itself
([review case operation](../operations/review-case.md)).

### Policy preview

A draft preview evaluates the active and draft policies on at most 20 current
report/proposal pairs, read in one database snapshot, without changing them or
re-reading the remote service. Title, authors, narrators, date, publisher,
language and cover are not previewed. Activation still checks that the active
policy and generation have not changed since. Preview and activation grant no
write permission. How the owner reads a preview is in the
[review experience](review-experience.md#policy); activation is in
[review cases](../operations/review-case.md#policy-activation).

## Qualification

An option becomes Automatic only through a current qualification record that
names its exact operations ([qualification records](rich-metadata-qualification.md)).
No record crosses media
([ADR-0073](../architecture/decisions/0073-audiobook-decision-qualification.md)).
On a recording, Automatic is withdrawn from every destination the audiobook
qualification record does not name, so an unqualified audiobook field is always
the owner's choice, never an authority failure. A candidate stays available to
the owner whether or not it is automatic.

### Options and defaults by media

"Fill" needs a known-empty target; "preserve" records no change. Each operation
still needs its own evidence and currentness. A blank audiobook entry is never
inherited from ebooks.

| Policy field | Option | Ebook | Audiobook |
| --- | --- | --- | --- |
| `title_layout` | `preserve` | fill or replace | title: fill, preserve, replace |
| `title_layout` | `title_and_subtitle` (default) | fill or replace | title: fill, preserve, replace; title/subtitle pair |
| `date_precision` | `year` (default) | fill or replace | none |
| `date_precision` | `full_date` | fill or replace | publishedDate: fill |
| `language_rendering` | `display_name` (default) | fill or respell | language: fill, normalize, preserve |
| `contributor_names` | `preserve` | fill or replace | authors: add, fill, normalize, preserve, replace |
| `contributor_names` | `canonical` (default) | fill or replace | authors and narrators: add, fill, normalize, preserve, replace |
| `publisher_preference` | `publisher` (default) | fill or replace | publisher: fill, preserve |
| `publisher_preference` | `imprint` | none | none |
| `subtitle` | `fill_empty` (default) | fill | subtitle: fill |
| `subtitle` | `corroborated_split` | none | subtitle: fill |
| `subtitle` | `replace_descriptive` | none | subtitle: preserve, replace |
| `series` | `primary_narrative` (default), `source_order`, `all_qualified` | fill | series: fill, preserve |
| `series` | `add_qualified` | none | series: add, fill, preserve |
| `series` | `replace_qualified` | none | series: fill, normalize, preserve, replace |
| `genres` | `fill_empty` (default) | fill | genres: fill, preserve |
| `genres` | `add` | add | genres: add, fill, preserve |
| `genres` | `replace` | replace | genres: add, fill, normalize, preserve, replace |
| `tags` | `off` (default) | none | none |
| `tags` | `fill_empty` | fill | tags: fill |
| `tags` | `add` | add | tags: add |
| `tags` | `replace` | replace | none |
| `description_fill` | `safe_plain_text` (default) | fill | description: fill, preserve |
| `description_fill` | `google_visible_text` | none | none |
| `description_fill` | `replace_safe_plain_text` | none | description: fill, preserve, replace |
| `isbn_form` | `preserve_exact` (default) | fill | isbn: fill, preserve |
| `isbn_form` | `prefer_isbn13` | fill | isbn: fill, normalize, replace |
| `cover_order` | `fixed_source_order` (default) | fill or replace | none |
| `cover_order` | `replace_if_strictly_better` | fill | cover: replace |
| `asin` | `preserve_exact` (default) | none | asin: fill |
| `asin` | `ebook_manifestation` | none | none |
| `explicit` | `unsupported` (default) | none | none |
| `explicit` | `adult_product` | none | explicit: fill, preserve, replace |

## Evidence

### What counts

Deterministic policy decides; an AI reviewer only recommends among supplied
proposals ([ADR-0004](../architecture/decisions/0004-ai-is-untrusted.md)). The
current target snapshot is authoritative for no-op detection, concurrency,
planning and verification, but its metadata is only one sourced observation.
Local evidence can close a field without a provider when policy allows it;
fields that need independent provider evidence go to the owner when it is
missing. Everything except provider-dependent fields keeps working with every
provider disabled.

Evidence is judged for applicability, independence and contradiction, not by
one confidence score. It includes EPUB package metadata and bounded front
matter, identifiers mined from the file, audio tags, chapters and duration,
spoken credits once the planned
[opening-audio evidence](opening-audio-evidence.md) exists, the target's
current state, catalogue records for the same work or
edition, and cross-format evidence for the same owned item. An embedded value is
not automatically true, and a catalogue value is not automatically
edition-applicable.

Agreement counts only between materially independent observations. Several
adapters repeating one upstream record are one source, and package metadata and
front matter from one file are one lineage. Equal normalized values collapse
into one choice. Identifiers are compared, after ISBN-10/ISBN-13
canonicalization, only within compatible namespaces. Resolving one field never
decides a sibling field.

Ambiguity (generic or very short titles, initial-only author names, swapped
fields, conflicting series orders, release-name debris) raises the evidence
needed for replacement. Closed case, whitespace and typographic folding, and
single-author comma inversion explain agreement only when the complete ordered
creator set and roles stay intact
([ADR-0018](../architecture/decisions/0018-closed-title-creator-comparison.md)).
An untyped creator role only corroborates a complete explicit-author set that
two provider authorities agree on, and never becomes an author.

Provider outage, timeout, quota, authentication failure and malformed or empty
responses are operational facts. They are never evidence that a value is
absent, and a failed lookup never vetoes a value supported elsewhere.

### Provider lanes

Each report derives and records its provider lanes
([metadata report](../operations/metadata-report.md)). A missing lane is a
coverage failure, and failed or stale lanes follow governed retry
([provider recovery](../operations/provider-recovery.md)); none is silently
skipped. One lane never blocks unrelated local evidence or another lane.

### Refusals

Each final evaluation keeps a field-specific refusal: missing eligible
evidence, contradicted work applicability, unproved association, missing
edition evidence, an unusable current value or an unavailable contributing
source. The first applicable refusal governs. For ISBN the order is malformed
or incomplete evidence, conflicting families, insufficient association,
unavailable source, then absent value. Library and Evidence read this recorded
evaluation; they do not recompute it. A missing record reads as "unavailable
recorded evaluation", and a generic reason means no more specific reason was
recorded; neither implies missing evidence or a conflict. A current suspension
is shown separately from the recorded reason.

## Field rules

### Title

A useful provider title wins when evidence ties it to the same work and nothing
material competes; one reliable provider can be enough
([ADR-0075](../architecture/decisions/0075-independent-evidence-led-titles.md)).
A checksum alone does not link an ISBN to the work; how titles are elected is
in the [ProposalSet](../operations/proposal-set.md#titles). File, package and
service titles are observations whose disagreement alone cannot veto stronger
evidence. A title that differs only by a dropped leading `The`, `A` or `An` is
the same title: the form with the article is kept or elected, and the bare form
is never offered. Subtitle and series are decided in their own fields. Material
competing works, authors or titles go to the owner.

Ebook automatic title replacement has three qualified operations:

- `isbn_creator_supported`: an ISBN linked to the work plus a complete ordered
  creator match. Straight and curly apostrophes compare equal.
- `provider_consensus_noise`: one exact local noisy title with one reversible
  pattern, byte-identical clean titles from at least two independent
  authorities, and no dissent. A colon subtitle elects only when its suffix is
  kept as the structured subtitle. Of bracketed suffixes, only `[retail]` and a
  bracketed valid ISBN, such as `[ISBN 978…]`, elect; others abstain.
- `decoration_tidy`: two provider-backed alternatives equal up to one closed
  decoration, removed in exactly one exact grammar step. Every provider title
  must fall in that class, and at least as many authorities must state the plain
  form as the decorated one. If the current title is plain, the result is a
  zero-write keep ([ADR-0067](../architecture/decisions/0067-decoration-equivalence-and-compound-admission.md)).

A supported unchanged title records one Automatic no-change decision with no
write or plan, so it bypasses target guards
([ADR-0079](../architecture/decisions/0079-supported-title-preservation-and-recovery.md)).
When the only eligible title equals the current one, the preservation is used
only if both carry identical citations and neither belongs to a combination.

An explicit ISBN/title material conflict stays the owner's choice even when a
candidate equals the current title. Automatic records the conflict without
suspending title automation.

A series name that equals the title (whole normalized tokens) may fill an empty
series beside a kept title. That grants no title change, sequence exemption or
series merge.

### Authors and narrators

Authors may be filled, normalized, reordered (ebooks only) or corrected when
identity evidence supports the people and roles. On a recording, the same
authors in another order are a preservation, never an Add. A package creator
placeholder cannot qualify an authors proposal on its own. Authorship
conflicts, unmappable roles and ambiguous collective or pseudonym choices go to
the owner.

`contributor_names:canonical:provider_consensus_display` changes the display
only when two independent providers agree on one exact display and the local
equivalent is the sole dissent. Its authority is the contributor-display
qualification record alone; ordinary `contributor_names:canonical` gains
nothing from it.

On both media, a candidate that only drops or moves accents of the current
members is preserved with the current spelling. Any other candidate that drops
or moves a member's accents is never automatic; one that adds accents is a
replacement. A candidate with a credit line ("Narrated by …", "Read by",
"Narrator: …") is never automatic and is offered as the provider's exact text;
a credit-line incumbent may still be replaced by a plain cast.

Narrator changes need `contributor_names:canonical`; Preserve does not qualify
them. A narrator replacement goes to the owner when the file's own narrator
credits share no member with the product's cast. A file tag naming several
narrators is never split.

### Subtitle

Ebook `subtitle:fill_empty` fills an empty subtitle only with a provider value
corroborated by another source: the book's own packaged subtitle, or a colon
segment stripped from another source's work title under the
[segment rule](../operations/proposal-set.md#subtitles). A source never
corroborates itself. For an empty subtitle, the labels `A Novel` and `Stories`
are generic marketing and never become a proposal, whether a provider or the
book's own package states them. When Subtitle is
Automatic, the fill owns the subtitle destination unless an acting
title-combination candidate already covers it.

Audiobook subtitles may fill an empty value or replace a recognized whole-field
award, bestseller or adaptation announcement with descriptive text. On either
medium, a title candidate with any part (after a colon, spaced dash, comma or
semicolon, or in an enclosure) that is such an announcement is never automatic.
A current title holding one may still be replaced by a plain title.

A corroborated title/subtitle split needs both options Automatic and both
current decisions and consents. One request writes the pair, both read-backs
must verify, and partial or uncertain results use the reconciliation path.

### Series

Automatic series fills empty targets only and keeps existing memberships. One
provider can supply the work backing. Absent numbers stay absent. Malformed
selected evidence and failure of a contributing lookup abstain; unrelated
providers cannot veto. A unique existing source bundle may resolve compatible
subset alternatives, in Ask me too, keeping its exact order and values; sources
are never unioned. Leading articles and trailing `Series`/`Trilogy` use the name
grammar. Known positions must agree exactly or as unsigned decimals. There is no
fuzzy matching, provider ranking or number inference. The decision keeps every
qualified membership, but an automatic ebook write sends only the first,
because Audiobookshelf takes one series value per write.

A series source must describe the target work: a complete ordered author
match, an exact edition title, or a one-step parenthesized series name attested
by that candidate
([ADR-0070](../architecture/decisions/0070-isbn-backed-series-identity.md)).
Every candidate in a target-backed ISBN lane must qualify.

Audiobook series Add keeps current associations. Complete-set replacement is a
separate choice and refuses conflicting sequence facts.

### Publication date and publisher

Ebook dates follow the
[publication candidate rule](../operations/proposal-set.md): local evidence plus
two independent provider correlations at the selected precision. Year,
month and day precision survive both date options, the write and later reads.

For audiobooks, publication means original work publication. Recording release,
copyright and edition dates do not vote. Year-only has no automatic effect.
Publisher fills; imprint does not. Publisher and imprint roles are compared
separately and real conflicts stay visible.

### Language

Language names and codes are grouped before election on both media
([evaluation rules](../operations/proposal-set.md#evaluation-rules)), and every
language is written as one display name, such as "English" or "Chinese
(Simplified)", whatever spelling the evidence used. A current value already
spelled that way stays unchanged. A current value of the same language spelled
another way, such as `en`, `eng` or `en-GB`, is respelled to its display name
automatically, as a fill is, and is never an owner question. Beside a different
language, a value that only respells the current one is not offered. Region is
not written; base or script disagreements stay manual. No language is
inferred.

### Description

Ebook `safe_plain_text` fills an empty description from one exact work-bound
provider record; a local or embedded description can be inspected but never
fills automatically. The text must be plain, not a whole-value catalogue marker
such as `(duplicate)` or a page and size line, and not mostly quoted praise.
When several remain, any ending at a visible cutoff is dropped and the fixed
order Hardcover, Google Books, Open Library chooses; if a remaining candidate
comes from a source outside that order, the choice abstains. Text is never
combined, and no length or word-count floor applies. An unavailable provider
does not change the order.

An audiobook synopsis prefers a safe nonempty summary over a short
description, falling back only when the summary is absent or empty. Among
equally preferred sources, stable provider and record order decides; length,
wording and vote counts never rank text. Unsafe text or competing text within
one record refuses automatic selection. A synopsis that
is only a credit line, a format word, a chapter or track marker, an encoder
watermark, empty markup or `n/a`, `none`, `unknown` or `tbd` describes nothing:
it never fills, and under `replace_safe_plain_text` such an incumbent is
replaceable.

On both media, a description that is mostly attributed praise (quoted spans
with attribution covering at least half its non-space characters) or that
opens with "Praise for" is never automatic or recommended. A praise incumbent
is preserved.

### Genres and tags

Genres and tags project one complete ordered work-bound source list without
synthesis or relabelling. Automatic genre fill treats closed creator display
equivalence as absence of contradiction, not as identity. Genuine competing
alternatives abstain; populated genres are preserved under fill-empty.

For audiobooks, a genre list holding only format words such as "Audiobook" is
empty, so fill-empty may fill it, and adding beside it is a fill. A format-only
candidate is no candidate.

Compatible complete tag lists may be unioned per source for fill or Add,
keeping owner members and every reference. Tags are off by default. Tag
replacement is a separate choice and never uses genres as tags.

### Identifiers

Valid typed identifiers may be added, and malformed current values replaced,
under ordinary field evidence. A valid current identifier is superseded only
when an embedded or content identifier and an independent provider agree on the
same canonical replacement.

One checksum-valid content ISBN establishes an edition only when an independent
format-appropriate provider returns the same canonical ISBN and the target,
work and media agree. Multiple content ISBNs, other-work claims, correlated
providers and edition, format, narrator, abridgement, dramatisation, omnibus or
cross-media conflicts stay unresolved. A spoken ISBN is audio evidence, not a
content ISBN. ISBN election does not depend on a cover image
([ADR-0080](../architecture/decisions/0080-isbn-evidence-without-image.md)).

Audiobook ASIN and ISBN operations need the recording association and valid
canonical values. ASIN fills only under `preserve_exact`. An audiobook ISBN is
not publisher-verified. Evidence also lists non-voting identifier observations
with their source and state; they are never corroboration.

### Cover

Work identity is mandatory for a cover; edition purity is not, and UK and US
variants are interchangeable. Ebook cover election is deterministic and does
not use the optional reviewer
([ADR-0059](../architecture/decisions/0059-deterministic-cover-election.md)).
Only confirmed same-edition and same-book candidates take part. Same edition
ranks first, then portrait aspect, pixel area, width, height, source precedence
and a stable reference. Reviewer observations explain but never admit, rank or
authorize. Automatic needs a confirmed candidate and no lookup for that report
running or being checked; lookups waiting to retry do not hold it back.
`replace_if_strictly_better` fills only a known-empty ebook cover. A strictly
better later candidate may supersede an automatic choice; an owner choice ends
automatic authority. What the owner may choose is in the
[review experience](review-experience.md#covers).

An audiobook cover is replaced only when the known incumbent's shorter edge is
below 300 pixels and an eligible portrait or square candidate is at least 500
pixels and twice that edge. Adequate, unknown or stale incumbents are kept.

The previous cover is kept as rollback evidence before any write.

### Explicit

`adult_product` may set `explicit` to true from positive source evidence on
audiobooks. False or absent evidence keeps the current value; it never clears
the flag or certifies clean content.

## Audiobook matching

How a recording is admitted is in the
[metadata report](../operations/metadata-report.md). For policy:

- Work facts need an applicable work relationship. Recording facts, such as
  narrators and identifiers, keep their own association requirements.
- One applicable source can suffice. A complete title and author match in a
  canonical ASIN lookup, or a bound local ASIN, supports work facts even when
  the recording is unresolved. Runtime alone and a lookup hint never establish
  work identity.
- One terminal ` (Unabridged)` may be ignored in matching when the rest of the
  title and the complete authors agree. With the provider runtime within 90
  seconds, one series decoration ("(Saga, Book 2)" or "Saga Book 2 - Title")
  may also be ignored when the recording's own series states it, and "J.R.R."
  matches "J. R. R.". Neither is a title rewrite.
- Language, abridgement, full-cast and part conflicts matter; unknown language
  is not a conflict. Media-health facts never vote on identity.
- Narrator agreement counts only when every plausible source gives the same
  complete cast, and grants no identifier authority.

A title replacement that deletes a series label asks the owner unless the
series is already populated, is filled automatically in the same outcome, or
the removed text is carried by an automatic subtitle split.

**Work-only review.** When the recording stays unresolved but each source has a
complete work witness, the owner may choose description, genres, series, series
position and a witnessed title cleanup. Automatic becomes Ask me and Ignore
stays Ignore; no automatic authority follows
([ADR-0073](../architecture/decisions/0073-audiobook-decision-qualification.md)).
The owner's view is in the
[review experience](review-experience.md#audiobooks).

A refused current value, such as a credit-line narrator or an announcement
title, is not kept automatically. The field says why and joins Attention only
when another value is offered.

## Attention

Attention holds only work a person can improve:

- two or more materially plausible candidates;
- contradictions policy cannot resolve;
- an unsupported but potentially actionable target capability;
- a high-impact non-destructive change outside qualified policy; or
- a destructive action needing authorization.

Operational failures appear in health and job views, not Attention. Automatic
rejections and title cleanup are closed but searchable. How fields are
classified and queued is in
[review cases](../operations/review-case.md#field-classification).

## Decision records

Every automatic decision records the resolved policy and its generation, the
rule, category and option applied, the qualification digest, and the outcome and
proposal it came from. Owner decisions, overrides and rollbacks are recorded
separately and shown in History and Activity.
