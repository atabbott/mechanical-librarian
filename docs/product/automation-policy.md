# Automation policy

For each metadata field, Mechanical Librarian decides automatically, asks you,
or leaves the field alone. It applies the standard in
[Decision quality and human attention](../../PRODUCT.md#decision-quality-and-human-attention).
How you see and change these decisions is in the
[review experience](review-experience.md).

## Principles

- Mechanical Librarian decides each field separately. It never accepts or
  rejects a whole item.
- Embedded file and audio evidence is first-class evidence.
- Reasonable evidence is enough when credible sources agree and no material
  conflict remains.
- No-ops, clearly worse values and inapplicable fields are rejected
  automatically.
- Only choices the evidence cannot settle reach Attention.
- Automatic decisions stay visible in History and Activity.
- Deletion, duplicate removal and other destructive operations are never
  automatic. They need a separate review and your explicit permission.
  Metadata is never written into media files ([scope](scope.md)).
- Curation never blocks reading or listening. Poor metadata, an unavailable
  source or a failed step leaves the item available with its earlier metadata.

The tolerance covers uncertainty about the book only. Checks on credentials,
write permission, current values, supported writes, backups, read-back and
recovery still apply, as do your decisions and your Ask me and Ignore settings
([PRODUCT.md](../../PRODUCT.md#decision-quality-and-human-attention)).

## Automatic, Ask me and Ignore

Each policy field has an option, which says how a value should look or be
chosen, and one of these settings:

- Automatic records a decision without you, but only when that exact option is
  qualified for that kind of media (see [Qualification](#qualification)).
  Otherwise Policy will not turn Automatic on for that option, and the Policy
  page shows no automatic effect.
- Ask me offers you every eligible choice. The field joins Attention only when
  a real choice remains.
- Ignore leaves the field unchanged. Its proposals are marked
  `policy_ignored`, with no Attention row and no automatic decision.
- Not supported leaves the explicit-content flag unchanged. It is fixed while
  the flag's option is `unsupported`.

An Automatic decision only records a choice
([ADR-0036](../architecture/decisions/0036-qualified-decision-only-automatic-policy.md)).
Writing it to the library is a separate step. It needs write permission,
unchanged evidence and library values, a backup and a read-back check
([ADR-0037](../architecture/decisions/0037-origin-neutral-guarded-field-effects.md)).
Your decision on a field always overrides Automatic for that field.

### Built-in policy

Each field's default option is marked (default) in
[options and defaults by media](#options-and-defaults-by-media). Every field
defaults to Automatic except `tags` and `asin` (Ask me) and `explicit` (Not
supported). A library may override any field. Choosing an option with no
automatic effect while the field is Automatic changes it to Ask me, and the
Policy page says so.

### Modes and suspensions

Policy runs in one of three modes: Human review, Preview or Automatic.
Automatic decisions are recorded and written only in Automatic. Preview records
what policy would do without deciding or writing, so you can start there.
Turning on Automatic reprocesses the works already prepared, decides each from
what Preview showed, then writes those decisions under your write permission.
An automatic decision recorded before you left Automatic decides nothing
afterwards, so it keeps no field out of Attention.

Automatic stops when something fails. While automatic decisions are paused or
suspended, work that would be automatic waits in Attention. If recording
automatic decisions fails for one category, only that category is suspended
(`automatic_admission_failed`). Any other recording failure causes a global
suspension (`automatic_authority_failed`), which stops Automatic for every
field. You recover a suspension in Policy, and recovering applies nothing by
itself ([review case operation](../operations/review-case.md)).

### Policy preview

The impact preview compares the active and draft policies on at most 20
current works, read at one moment. It changes nothing and does not read
Audiobookshelf again. Title, authors, narrators, date, publisher, language and
cover are not previewed. Activating still checks that the active policy has not
changed, or been reactivated, since. Neither step grants write permission.
How you read a preview is in the
[review experience](review-experience.md#policy). Activation is in
[review cases](../operations/review-case.md#policy-activation).

## Qualification

An option can be Automatic only when a current qualification record names its
exact operations ([qualification records](rich-metadata-qualification.md)). An
ebook record never qualifies an audiobook option, or the reverse
([ADR-0073](../architecture/decisions/0073-audiobook-decision-qualification.md)).
On an audiobook, Automatic is withdrawn from every field the audiobook record
does not name. Those fields are your choice and never cause a suspension. A
proposed value stays available to you whether or not it is automatic.

### Options and defaults by media

"Fill" needs a field known to be empty; "preserve" records no change. Each
operation still needs its own evidence and an up-to-date library value. An
audiobook "none" never takes the ebook entry.

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
| `cover_order` | `fixed_source_order` (default) | fill | none |
| `asin` | `preserve_exact` (default) | none | asin: fill |
| `asin` | `ebook_manifestation` | none | none |
| `explicit` | `unsupported` (default) | none | none |
| `explicit` | `adult_product` | none | explicit: fill, preserve, replace |

## Evidence

Fixed rules decide. An AI reviewer only recommends among the proposals it is
given ([ADR-0004](../architecture/decisions/0004-ai-is-untrusted.md)).
Mechanical Librarian checks the library's current values to spot no-ops and
changes made in the meantime, to plan a write and to check the read-back. As
evidence, they are one source among others. Local evidence can settle a field
without a metadata source when policy allows it. A field that needs independent
source evidence goes to you when that evidence is missing. Everything else works
with every metadata source turned off.

Evidence is judged on whether it applies, is independent and is contradicted,
not by one confidence score. It includes the EPUB package metadata, opening
pages and identifiers in the file, and audio tags, chapters and duration.
Spoken credits join once the planned
[opening-audio evidence](opening-audio-evidence.md) exists. Evidence also
includes the library's current values, catalogue records for the same work or
edition, and another format of the same owned item. An embedded value is not
automatically true, and a catalogue value does not automatically apply to this
edition.

Agreement counts only between independent sources. Several metadata sources
repeating one upstream record are one source, and so are package metadata and
front matter from one file. Equal values become one choice after normalising.
Identifiers are compared only within compatible types, after converting between
ISBN-10 and ISBN-13. Deciding one field never decides another.

Ambiguity raises the evidence needed to replace a value: generic or very short
titles, initials for author names, swapped fields, conflicting series orders
and leftover release-name text. Case, spacing and typography differences from
a fixed set, and one author written "Surname, Forename", count as agreement
only when the complete, ordered creators and roles stay intact
([ADR-0018](../architecture/decisions/0018-closed-title-creator-comparison.md)).
A creator with no stated role can only confirm a complete list of named authors
that two sources agree on, and never becomes an author.

Source outages, timeouts, quotas, authentication failures and malformed or
empty responses are operational facts. They are never evidence that a value is
absent, and a failed lookup never vetoes a value supported elsewhere.

### Source lookups

Each report records which source lookups it needs
([metadata report](../operations/metadata-report.md)). A missing lookup counts
as a coverage failure. A failed or out-of-date lookup is retried under the
[recovery rules](../operations/provider-recovery.md), and none is skipped
silently. One lookup never blocks unrelated local evidence or another lookup.

### Why nothing was chosen

When Automatic chooses nothing for a field, it records the first reason that
applies. The reasons are missing evidence, a value for another work, an
unproved link to this work, no evidence for this edition, an unusable current
value and an unreadable source. For ISBN the order is unreadable or incomplete
evidence, conflicting ISBNs, a weak link to the recording, an unreadable
source, then no ISBN. Library and Evidence show this recorded reason without
working it out again. When no specific reason was recorded, Library and
Evidence say so. That never means evidence is missing or the sources conflict.
A current suspension is said first, in its own sentence.

## Title

A useful title from a metadata source wins when evidence ties it to the same
work and nothing material competes. One reliable source can be enough
([ADR-0075](../architecture/decisions/0075-independent-evidence-led-titles.md)).
A valid checksum alone does not link an ISBN to the work. How titles are chosen
is in the [ProposalSet](../operations/proposal-set.md#titles). Disagreement
among file, package and Audiobookshelf titles cannot by itself veto stronger
evidence. A title that differs only by a dropped leading `The`, `A` or `An` is
the same title. The form with the article is kept or chosen, and the bare form
is never offered. "The" and "A" still make different titles. Subtitle and
series are decided in their own fields. Material conflicts over the work,
authors or title go to you.

Ebook automatic title replacement has three qualified operations:

- `isbn_creator_supported`: an ISBN linked to the work plus a complete ordered
  creator match. Straight and curly apostrophes compare equal.
- `provider_consensus_noise`: exactly one local title with one reversible noise
  pattern, the identical clean title from at least two independent sources,
  and no dissent. A colon subtitle is chosen only when its suffix is kept as
  the subtitle field. Of bracketed suffixes, only `[retail]` and a bracketed
  valid ISBN, such as `[ISBN 978…]`, qualify. Automatic abstains on others.
- `decoration_tidy`: two source-backed alternatives that differ by one
  decoration from a fixed list, removed in exactly one grammar step. Every
  source title must take one of the two forms, and at least as many sources
  must state the plain form as the decorated one. If the current title is
  already plain, it is kept and nothing is written
  ([ADR-0067](../architecture/decisions/0067-decoration-equivalence-and-compound-admission.md)).

A supported title that stays the same records one Automatic no-change decision
with no write or plan, so the checks before a write do not apply
([ADR-0079](../architecture/decisions/0079-supported-title-preservation-and-recovery.md)).
When the only eligible title equals the current one, it is kept this way only
if both cite the same evidence and neither is part of a combination.

When the ISBN and title explicitly conflict on something material, the title
stays your choice even if a proposed value equals the current title. Automatic
records the conflict without suspending automatic titles.

A series name that equals the title (whole normalised tokens) may fill an empty
series beside a kept title. That grants no title change, sequence exemption or
series merge.

## Authors and narrators

Authors may be filled, normalised, reordered (ebooks only) or corrected when
identity evidence supports the people and roles. On a recording, the same
authors in another order are a preservation, never an Add. A package creator
placeholder cannot qualify an authors proposal on its own. Authorship
conflicts, unmappable roles and ambiguous collective or pseudonym choices go to
you.

`contributor_names:canonical:provider_consensus_display` changes how a name is
written only when two independent sources agree on one exact form and the
local form is the only dissent. Only the contributor-display qualification
record qualifies it, and ordinary `contributor_names:canonical` gains nothing
from that record.

On both media, a proposal that only drops or moves accents of the current names
keeps the current spelling. Any other proposal that drops or moves a name's
accents is never automatic, and one that adds accents is a replacement. A
proposal with a credit line ("Narrated by …", "Read by", "Narrator: …") is
never automatic and is offered as the source's exact text. A current value with
a credit line may still be replaced by a plain cast.

Narrator changes need `contributor_names:canonical`, not `preserve`. A narrator
replacement goes to you when the file's own narrator credits share no name with
the source's cast. A file tag naming several narrators is never split.

## Subtitle

Ebook `subtitle:fill_empty` fills an empty subtitle only with a metadata
source's value that another source confirms. That source is the book's own
packaged subtitle, or a colon segment from another source's work title under
the [segment rule](../operations/proposal-set.md#subtitles). A source never
confirms itself. For an empty subtitle, `A Novel` and `Stories` are generic
marketing and never become a proposal, even when the book's own package states
them. When Subtitle is Automatic, the fill decides the subtitle unless an
automatic title and subtitle split already covers it.

Audiobook subtitles may fill an empty value, or replace a subtitle that is only
an award, bestseller or adaptation announcement with descriptive text. On
either medium, a proposed title is never automatic when any part is such an
announcement. A part follows a colon, an underscore and a space, a spaced dash,
a comma or a semicolon, or sits in parentheses or square brackets. A current
title holding one may still be replaced by a plain title.

On either medium, a subtitle that only restates the series is never filled or
replaced for you. It stays your choice. Such a subtitle is a position the
series rules read, such as "Saga, Book 3" or "Saga #2". It can also be a stated
series name in a fixed frame: "A Saga Novel", "Book 3 of Saga", "The Third Saga
Novel" or "Saga 4". A series name is stated when the book's series field, a
source or a proposed series gives it. Other wording is left alone, so "A Tale
of Saga" can still be written.

A confirmed title and subtitle split needs `title_layout` and `subtitle` both
Automatic, each with a current decision and write permission. One request
writes the pair, and both read-backs must match. A partial or uncertain result
is read back and settled like any interrupted write.

## Series

Automatic series fills empty fields only and keeps existing memberships. One
metadata source can tie the series to the work. A missing number stays missing.
Malformed chosen evidence, or a failed lookup it depends on, makes Automatic
abstain. Unrelated sources cannot veto. When exactly one source's list contains
each other proposal as a compatible subset, it may be chosen, in Ask me too,
with its exact order and values. Lists from different sources are never merged.
Leading articles and a trailing `Series` or `Trilogy` follow the series name
rules. Known positions must agree exactly or as unsigned decimals. Names are
never fuzzy-matched, sources never ranked and numbers never guessed. The
decision keeps every supported membership, but an automatic ebook write sends
only the first, because Audiobookshelf takes one series value per write.

A series source must describe this work: a complete ordered author match, an
exact edition title, or a one-step parenthesised series name that the same
proposal states
([ADR-0070](../architecture/decisions/0070-isbn-backed-series-identity.md)).
Every proposal from a lookup by the book's own ISBN must qualify.

Audiobook series Add keeps current memberships. Replacing the complete set is a
separate option and refuses conflicting series numbers.

## Publication date and publisher

Ebook dates follow the
[publication date rule](../operations/proposal-set.md): local evidence plus two
independent sources that agree with it at the selected precision. Year, month
and day precision survive both date options, the write and later reads.

For audiobooks, publication means original work publication. Recording release,
copyright and edition dates do not vote. Year-only has no automatic effect.
Publisher fills; imprint does not. Publisher and imprint roles are compared
separately and real conflicts stay visible.

## Language

Language names and codes are grouped before a choice is made, on both media
([evaluation rules](../operations/proposal-set.md#evaluation-rules)). Every
language is written as one display name, such as "English" or "Chinese
(Simplified)", whatever spelling the evidence used. A current value already
spelled that way stays unchanged. A current value of the same language spelled
another way, such as `en`, `eng` or `en-GB`, is respelled automatically, as a
fill is, and never asks you. The exception is an audiobook regional tag such as
`en-GB`, which still asks: dropping the region is a replacement the audiobook
qualification never measured. Beside a different language, a value that only
respells the current one is not offered. Region is not written, and base or
script disagreements stay your choice. No language is inferred.

## Description

Ebook `safe_plain_text` fills an empty description from one source record tied
to this exact work. A local or embedded description can be inspected but never
fills automatically. The text must be plain, must not consist of a catalogue
marker such as `(duplicate)` or a page and size line, and must not be mostly
quoted praise. When
several remain, any ending at a visible cutoff is dropped, and the fixed order
Hardcover, Google Books, Open Library chooses. If one comes from a source
outside that order, Automatic abstains. Text is never combined, and no minimum
length or word count applies. An unavailable source does not change the order.

An audiobook synopsis prefers a safe, non-empty summary over a short
description, which is used only when the summary is missing or empty. Among
equally preferred sources, a fixed source and record order decides. Length,
wording and vote counts never rank text. Unsafe text, or competing text within
one record, stops automatic selection. A synopsis that is only a credit line, a
format word, a chapter or track marker, an encoder watermark, empty markup or
`n/a`, `none`, `unknown` or `tbd` describes nothing. It never fills, and under
`replace_safe_plain_text` a current synopsis like that can be replaced.

On both media, a description that opens with "Praise for", or that is mostly
attributed praise, is never automatic or recommended. Mostly means quoted,
attributed passages cover at least half its non-space characters. A current
description of praise is kept.

## Genres and tags

Genres and tags copy one complete, ordered list from a source tied to this
work, with nothing added or renamed. For an automatic genre fill, creator names
that differ only in a fixed set of display forms count as no contradiction.
They do not prove the same work. When real alternatives compete, Automatic
abstains. Under `fill_empty`, genres already set are kept.

For audiobooks, a genre list holding only format words such as "Audiobook" is
empty, so fill-empty may fill it, and adding beside it is a fill. A proposed
list of only format words does not count.

Compatible complete tag lists may be combined per source for fill or Add,
keeping your tags and every reference. Tags are off by default. While they are
off, no tags choice asks for review, whatever the field's setting. Tag
replacement is a separate option and never uses genres as tags.

## Identifiers

Valid typed identifiers may be added, and malformed current values replaced,
under ordinary field evidence. A valid current identifier is replaced only when
a file or content identifier and an independent source agree on the same
canonical replacement.

One checksum-valid content ISBN establishes an edition only when an independent
source for that format returns the same canonical ISBN and the item, work and
media agree. Several content ISBNs, other-work claims, sources repeating one
upstream record, and edition, format, narrator, abridgement, dramatisation,
omnibus or cross-media conflicts stay unresolved. A spoken ISBN is audio
evidence, not a content ISBN. Choosing an ISBN does not depend on a cover image
([ADR-0080](../architecture/decisions/0080-isbn-evidence-without-image.md)).

Audiobook ASIN and ISBN operations need a confirmed link to the recording and
valid canonical values. ASIN fills only under `preserve_exact`. An audiobook
ISBN is not publisher-verified. Evidence also lists identifiers that do not
vote, with their source and state. They never confirm a value.

## Cover

A cover must belong to the same work but need not be the same edition, and UK
and US variants are interchangeable. Covers are chosen by fixed, measured rules,
without the optional reviewer
([ADR-0093](../architecture/decisions/0093-measured-cover-rules-and-current-cover-protection.md)).
Only covers confirmed for the same edition or book take part. The rules drop
the wrong shape for the medium (square art for an ebook; book-shaped art for an
audiobook when square art at least 300 pixels on its short side exists), keep
the sharpest copy of the same art, and drop covers under 300 pixels on the
short side. What is left ranks same edition first, then pixel area, width,
height, source order and a stable reference. Sizes are read as the image
displays. The reviewer's notes can explain a cover but never put one forward,
rank it or approve it.

The current cover stays unless it has a fault: the wrong shape, under 300
pixels, unreadable, a sharper copy of the same art available, or an edition
labelled in a language the library does not hold. A title that names a
different book from most covers holds the cover until the title is settled.
Size alone never replaces a readable cover that has no fault.

Policy → Curation → Current covers says what happens to a current cover today,
while no release can replace one automatically (below):

| Replace a current cover | What happens |
| --- | --- |
| Only with a fault (default) | A cover with a fault gets a suggested replacement. Other covers are kept. |
| When another is preferred | A cover gets a suggested replacement when another is preferred, including a cover you set in Audiobookshelf. |
| Never | Covers are never replaced automatically. A cover with a fault gets a suggested replacement. |

Saving Current covers compares each prepared cover again on the next scan,
which the save requests, so the suggestions follow the new setting.

Prefer the edition's cover, off by default, suggests a cover from the book's
own edition over a cover of the book in general; only When another is
preferred lets it switch automatically.

Replacing a cover automatically needs its own qualification, which no release
has yet, so today every such replacement is a suggestion in Covers' To decide.
It also needs room to keep the replaced cover to put back; while that store is
full, the replacement is suggested. Automatic fills only an ebook cover known
to be empty, with a confirmed cover and no lookup for that report running or
being checked; lookups waiting to retry do not hold it back. An audiobook cover
is never filled or replaced automatically. Once you choose a cover, Automatic
no longer changes it, and changing Current covers stops any automatic change
not yet written. What you may choose is in the
[review experience](review-experience.md#covers).

Book matching in Policy, set wider than "Confirmed matches only", lets a cover
from a source record with the book's title and author be labelled "Same title
and author" for you to choose. Such a cover never takes part in an automatic
choice under any option, including the deterministic switches and fills and
automatic mode, until the owner-run qualification in #1418 passes
([ADR-0096](../architecture/decisions/0096-owner-chosen-work-identity-strictness.md)).

The previous cover is kept as rollback evidence before any write.

## Explicit

`adult_product` may set `explicit` to true from positive source evidence on
audiobooks. False or absent evidence keeps the current value. It never clears
the flag or certifies clean content.

## Audiobook matching

How a recording is matched is in the
[metadata report](../operations/metadata-report.md). For policy:

- Work facts need an applicable work relationship. Recording facts, such as
  narrators and identifiers, keep their own association requirements.
- One applicable source can suffice. A complete title and author match in a
  canonical ASIN lookup supports work facts even when the recording is
  unresolved. So does an ASIN read from the recording's own tag, sidecar or
  file path. Running time alone and a lookup hint never
  establish which work it is.
- One terminal ` (Unabridged)` may be ignored in matching when the rest of the
  title and the complete authors agree. When the source's running time is
  within 90 seconds of the file's, two more differences may be ignored. One is
  a series decoration that the recording's own series states, such as
  "(Saga, Book 2)" or "Saga Book 2 - Title". The other is "J.R.R." against
  "J. R. R.". Ignoring them never changes the title.
- Language, abridgement, full-cast and part conflicts matter. An unknown
  language is not a conflict. Media-health facts never vote on the match.
- Narrator agreement counts only when every plausible source gives the same
  complete cast, and it never confirms an identifier.

A title replacement that deletes a series label asks you. It does not when the
series is already set or filled automatically in the same outcome, or when an
automatic subtitle split keeps the removed text. The removed text is read in
parts split at the same separators, including an underscore and a space
("Title_ A Saga Novel"). A series named only by a subtitle label such as
"Saga, Book 4" counts as one this book states.

When the recording stays unresolved but each source fully identifies the work,
some work fields are still yours to choose. They are description, genres,
series, series position, a supported title cleanup and the work's first
publication date. A cover is offered when the current one is empty or under 300
pixels on its shorter edge. The new one must be portrait or square and tied to
this work. Narrators are offered only for an empty field, from Hardcover audio
editions Audible cannot confirm, and always as an unsettled choice
([ADR-0090](../architecture/decisions/0090-hardcover-audio-edition-witness.md)).
A library item with no authors cannot identify a work, so authors are never
offered. Automatic becomes Ask me and Ignore stays Ignore, so no field in this
case is automatic
([ADR-0073](../architecture/decisions/0073-audiobook-decision-qualification.md)).
Your view of it is in the [review experience](review-experience.md#audiobooks).

A current value the rules reject, such as a narrator with a credit line or a
title with an announcement, is not kept automatically. The field says why and
joins Attention only when another value is offered.

## Attention

Attention holds only what you can improve:

- two or more plausible values that compete
- contradictions policy cannot resolve
- an unsupported library capability that could still be acted on
- a high-impact change that destroys nothing but is outside what Automatic may
  do
- a destructive action that needs your permission

Operational failures appear in health and job views, not Attention. Automatic
rejections and title cleanup are closed but searchable. How fields are
classified and queued is in
[review cases](../operations/review-case.md#field-classification).

## Decision records

Every automatic decision records the resolved policy and its generation, the
rule, category and option applied, the qualification digest, and the outcome and
proposal it came from. Your decisions, overrides and rollbacks are recorded
separately and shown in History and Activity.
