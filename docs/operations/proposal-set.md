# ProposalSets

A ProposalSet is the immutable list of proposed values for one exact current
[metadata report](metadata-report.md). Preparation creates one for every report
and passes it to [review-case admission](review-case.md). It proposes; it never
decides, plans or writes.

The `CreateProposalSet` operation reopens the exact report, revalidates the
current target, selected media, Audiobookshelf cover and every referenced
private cover artifact, then evaluates the built-in `fixed-conservative-v39`
policy. It never contacts a provider. Success is one canonical
`proposal-set-v8` JSON document and a newline; failure is one value-free
diagnostic. Contributors run it through the `proposal-create` and
`proposal-inspect` [workbench](../development/contributor-workbench.md)
scenarios. Field-by-field automation rules are in the
[automation policy](../product/automation-policy.md).

## Contents

Every ProposalSet is bound to the exact report ID, derived target fingerprint,
policy version and resolved policy identity. It holds exactly one ordered
result for each of title, subtitle, authors, narrators, series, genres,
publishedYear, publishedDate, publisher, description, isbn, asin, language,
explicit, tags and cover.

Each result has one outcome:

| Outcome | Meaning |
| --- | --- |
| `propose_change` | Exactly one selected safe candidate. |
| `acceptable_choice` | Two or more acceptable publisher candidates, none selected. Not a conflict. |
| `unresolved_choice` | Bounded candidates, none selected because a contradiction or another rule remains. |
| `keep` | Normally one positive preservation alternative. |
| `defer` | No candidate; evidence or capability prevents a decision. |
| `reject` | Reserved for explicit policy rejection; the evaluator never emits it. |
| `ineligible` | No choice. |

The active metadata profile is replayed from its persisted per-library
resolution. Ask leaves results for owner review. Ignore turns the category
into a non-actionable `policy_ignored` result before combinations are formed.
Automatic applies only where the active policy and safety posture qualify, and
creates no plan or write. The ID is derived from the canonical bytes, not
stored in the document.

### Alternatives

An alternative keeps only its bounded value, work, edition or cover scope,
assessment, stable evidence references and hashed correlation groups. It never
copies report excerpts, raw provider responses, URLs, paths or image bytes.
Provider access times stay in the referenced report.

### Bibliographic combinations

A separate `bibliographic-relationships-v1` projection lists the allowed title,
subtitle and series combinations. It never changes a result, alternative or
source string.

- Every combination covers all three destinations. A candidate selection
  points to an admitted alternative; a preserve selection binds the exact
  current, missing, null or empty target field.
- Only structured claims from one source representation are grouped. A clean
  title with at least two independent correlation groups may replace a
  decorated title inside an otherwise coherent tuple.
- A subtitle or series name contained in the title is vetoed as duplicated
  text, never split or rewritten.
- Several same-scope titles from one source, or a work title that disagrees
  with the edition title, stay report provenance, not alternatives.
- If a source has dependent candidates but no complete safe tuple survives,
  each affected destination with alternatives is `unresolved_choice` with
  `no_safe_bibliographic_combination`, and review marks all three fields
  manual.

## Evaluation rules

The evaluator uses no scores, provider priority, weights, fuzzy matching or
AI.

- **Keep** needs positive preservation evidence for a populated value.
- **Replacement** of a populated value normally needs one unique value from at
  least two materially independent correlation groups; otherwise bounded
  choices are `unresolved_choice`. Connections to one upstream adapter are one
  group. Hardcover, Inventaire and Open Library are distinct groups.
- **Linked fields** stay whole: ordered author and narrator lists, series name
  and sequence, and date and year are never edited piecemeal. Untyped
  contributor evidence never becomes a partial narrator list.
- **Description, genres and tags** are never merged, replaced or cleaned when
  populated; candidates may only fill an empty field. Grouped description
  evidence uses an eligible provider's exact text when one exists.
- **Language** values in one base-and-script class (`English`, `en`, `eng`,
  regional variants) form one alternative that keeps every reference. `bh`
  resolves to `bih`, and `und` gives no authority
  ([ADR-0065](../architecture/decisions/0065-language-equivalence-classes.md)).
- **Publisher** and imprint observations bound to the edition are separate
  acceptable alternatives. A provider observation also needs an `accepted`
  ISBN relation on the same candidate. Work-scoped, unrelated, malformed and
  target-only statements give no authority.
- **ISBN** needs an `accepted` report relationship proving valid,
  edition-applicable evidence; unresolved applicability defers. ISBN-13 and
  ASIN values are never manufactured. An empty ISBN may be filled from an
  exactly bound ebook edition claim without a cover image, given a resolved
  work, compatible publication and format facts, and exactly one verified ISBN
  family; the value follows `preserve_exact` or `prefer_isbn13`
  ([ADR-0080](../architecture/decisions/0080-isbn-evidence-without-image.md)).
- **Explicit** needs direct evidence; absence supports neither `false` nor
  `keep`.
- **Placeholders**: an `Unknown` package creator is not an author candidate.
- **Wrong-work** provider evidence cannot supply genre or publication
  authority.

### Titles

A title may be elected by any of these, each with its own reason:

- **ISBN and creator** (`isbn_creator_supported_title`): a provider title from
  a successful ISBN lane plus positive creator identity. One provider is
  enough, and same-work print and ebook ISBNs may coexist. Competing work,
  author, title or variant evidence withholds it
  ([ADR-0075](../architecture/decisions/0075-independent-evidence-led-titles.md)).
- **Provider consensus**: every provider title claim states the same clean
  form. Its colon and series abstentions run after the agreement gate.
- **Decoration equivalence**: runs next, on a title held only by
  `competing_evidence`, when the decorated and plain forms both carry provider
  evidence and differ byte for byte only by the decoration, and the current
  value equals one of them. It keeps (`decoration_equivalent_<family>`) or
  tidies (`decoration_tidy_<family>`), subject to closing guards
  ([ADR-0067](../architecture/decisions/0067-decoration-equivalence-and-compound-admission.md)).

A collapsed title keeps one alternative; the removed decorated form adds no
evidence for subtitle or series.

A supported title preservation's no-change witness binds that exact outcome
and cannot select a replacement. Title recovery re-evaluates the current
ProposalSet without new evidence
([ADR-0079](../architecture/decisions/0079-supported-title-preservation-and-recovery.md)).

### Subtitles

On the competing seam only the book's own explicit subtitle claim elects
(`local_attested_subtitle`). A colon segment stripped from a title fills a
subtitle only through the automatic projection, and one rule,
`segment_corroboration`, judges it on both seams. A generic segment is
rejected. Otherwise a segment corroborates only if no provider states a
different subtitle (`subtitle_segment_provider_dissent`), at least one provider
carries the segment, and the plain form is at least as well supported as the
decorated one, counting the local title
(`subtitle_segment_decorated_consensus`). An uncorroborated segment reports
`subtitle_segment_uncorroborated`.

### Authors and contributors

Authors need complete creator sets. A neutral package set counts only when an
explicit opening author set or an independent provider set confirms every
member. Local-only fill or preservation is supported; local-only replacement
of populated authors stays manual.

The canonical contributor display elects a provider form only when two
independent authorities state the same complete list and the local list is the
only equivalent dissent; the local claims stay as provenance. A bracketed
source suffix grants election authority only when it is exactly `[retail]`;
any other bracketed suffix abstains.

### Series

Series alternatives keep every admitted membership; the automatic rules are in
the [automation policy](../product/automation-policy.md). Provider
series output with no name is not evidence. A named incomplete or unpositioned
membership, a real competing alternative, or a failed ISBN, WORK or ASIN
lookup that could have contributed keeps the field abstained, and the reason
names the failed kind. A series may share its name with a preserved title
(kept, held or set to Ignore) when it fills an empty series.

### Covers

An existing verified Audiobookshelf cover is kept. An empty target gets a
selected cover only when the complete bounded gallery has exactly one distinct
safe artifact. Competing complete galleries are `unresolved_choice`.

If another source ends incomplete (for example by timeout), a cover with a
usable artifact stays an owner choice (`cover_gallery_incomplete`,
`owner_cover_choice_required`), and one with none defers. Incomplete
acquisition never proves the target empty or invents a second choice. A
source with the same normalized preview as the target cover never qualifies a
replacement through larger dimensions.

## Publication candidate sufficiency

For an ebook, a `publishedDate` candidate needs local publication evidence and
at least two independent provider authority correlations. It is one
`manifestation_publication` alternative, keeping the supporting references.
Several connections to one authority are one correlation. A provider failure
is no vote, not absence and not disagreement. `publishedYear` is a fixed
redundant-context result with no alternatives.

Provider dates vote only if they apply to the manifestation, including the
matching valid local ISBN family. Wrong-work evidence stays visible but cannot
vote; unknown work identity neither disqualifies nor supports. Work-first,
copyright, modification and unknown-event dates never vote.

The candidate takes the finest precision (day, month or year) at which every
admitted observation agrees, so a local year with two agreeing provider days
supports only the year; conflicting years give no candidate. A timestamp with
`Z` or an exact signed `HH:MM` offset supports its stated calendar day without
conversion. Naive timestamps and offsets with seconds do not vote. No missing
component is invented.

A candidate whose year differs from the target's current `publishedYear` is
`unresolved_choice` (`date_year_conflict`). A candidate is not a
recommendation or write authority, and Ask routing has no `publishedDate`
destination, so a publication candidate alone creates no Ask owner action.

## Currentness and persistence

External target, media, cover and artifact checks finish before the insert
transaction, which then checks that the report still has its expected digest
and is still current for its whole scope. Any drift inserts nothing. Later
changes are caught when admission checks freshness again.

`proposal_sets` is append-only, with no current pointer, lifecycle or job
state. Identical inputs give the same ID and bytes. Earlier policy versions
and schemas are refused before any change; rebuild from a clean state
([compatibility](../reference/compatibility.md)).
