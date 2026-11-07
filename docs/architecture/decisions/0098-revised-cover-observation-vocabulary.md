# ADR-0098: Revise the cover observation vocabulary and keep observations advisory

- Status: proposed
- Date: 2026-11-07
- Owners: project maintainers
- Issues: #1417
- Revises: ADR-0046 (the observation vocabulary and its identities)
- Depends on: ADR-0093 (shape and size decided from pixels)
- Supersedes: none
- Superseded by: none

## Context

The cover observer returns closed visual observations for each admitted
candidate (ADR-0046). Since #865 it has twelve dimensions: medium,
composition, promotional text, tie-in branding, placeholder, reproduction,
overlay or marking, format branding, explicit imagery, cover-text language,
series styling and thumbnail legibility. All of them are advice: no admission,
exclusion, order or automatic gate reads them.

The study behind #1363 tested whether any of them could do more. On
2026-10-24 an owner labelled 201 cover considerations (771 covers), six vision
models' observations were scored against those labels, 39 model and owner
disagreements were adjudicated, and one pre-registered, one-shot holdout of 57
decisive cases was run. It found:

- **Promotional text** is on 65% of chosen covers and 62% of rejected ones, so
  its presence cannot rank. The owner separately marked prominent promotion,
  which the vocabulary cannot express.
- **No dimension qualifies as a single-model veto** (precision lower bound at
  least 0.9, with at least 15 owner positives). Format branding comes closest
  (23 of 24, lower bound 0.86), but every case also had the wrong shape, which
  ADR-0093 detects from pixels. Tie-in precision is 0.45, because a small
  "now a major motion picture" line and full film art share one value.
- **Placeholders and non-cover images** were not detected at all.
- **Reproduction** misses size problems at the 320×480 preview, and
  thumbnail legibility measures the preview rather than the cover.

ADR-0093 (proposed) decides shape, same-art copies and the size floor from
decoded pixels and reads no observation. This revision relies on it: v4
activates no earlier than ADR-0093's stage.

## Decision

### The revised vocabulary

Eleven dimensions. Every dimension keeps `unknown` for evidence that does not
support another value.

| Dimension | Closed values besides `unknown` | Change |
|---|---|---|
| `composition` | `minimal`, `moderate`, `busy` | `busy` replaces `dense`; `moderate` is a cover that is neither, which today can only be `unknown`. `moderate` deliberately extends #1417's listed boundary, so that `unknown` does not mean both "cannot tell" and "neither" |
| `promotional_text` | `absent`, `minor`, `prominent` | prominent is praise or a selling claim that rivals the title or author in size, or fills a large share of the design |
| `platform_banner` | `absent`, `present` | new: a retailer, subscription or platform banner or logo strip across the design; look-inside badges, price stickers and library stamps stay in `overlay_marking` |
| `tie_in` | `absent`, `franchise_text_only`, `adaptation_sticker_or_banner`, `adaptation_art` | replaces `tie_in_branding`; splits adaptation text, a sticker or banner over the book's own art, and adaptation art or cast photography |
| `cover_image_kind` | `front_cover`, `designed_placeholder`, `non_cover_image` | replaces `placeholder`; a designed placeholder keeps its meaning, a generic no-cover tile or publisher template; a non-cover image is an author photo, back cover, interior page, spine or a logo alone |
| `reproduction` | `clean`, `degraded` | limited to physical or processing defects visible at the preview: curl, moire, skew, a page edge, glare, heavy compression artefacts. Upscaling and resolution leave it |
| `overlay_marking` | `absent`, `present` | unchanged |
| `format_branding` | `absent`, `text_edition`, `audiobook` | unchanged |
| `explicit_imagery` | `absent`, `suggestive`, `explicit` | unchanged |
| `non_english_text` | `none`, `minor`, `title_or_majority` | replaces `cover_text_language`; never transcribes |
| `series_styling` | `standalone`, `series_branded`, `omnibus_or_set` | unchanged |

`medium` and `thumbnail_legibility` are dropped. Size is judged from decoded
pixels (ADR-0093), never from an observation. `non_english_text` never sets
an edition language: ADR-0093's language issue reads the provider's edition
label, and the packet's provider language context is hostile evidence it must
not use.

The vocabulary stays per cover and policy neutral. It projects no confidence,
quality, preference, ranking, identity or winner.

### New identities, together

The instruction, taxonomy reference, output schema
(`cover-observer-output-v3`) and report (`cover-observation-report-v4`)
change together. One shared instruction serves every reviewer model, with no
model-specific wording, inside the fixed 4,096-byte bound. A draft with the
meanings above measured 4,000 bytes. If wording must grow, meanings are
shortened; the bound is not raised.

The output schema stays closed: every dimension required, no additional
properties, exactly one row per admitted candidate, and local admission
rejects missing, repeated, invented, extra or out-of-vocabulary rows and
rechecks every binding, as ADR-0046 requires.

Reports made under v1 to v3 stay readable against their own taxonomy, output
schema and instruction, and keep their recorded observer reference and model
label, as history. The observer identity is rechecked against the live
reviewer profile only on the current path. Earlier reports never count as
current observations and are never rewritten. A current
consideration with no v4 report shows its visual checks as unavailable, never
as clean.

### The consideration replay identity

Every cover consideration's replay identity includes the instruction digest,
the observation and output schema versions and the taxonomy reference, whether
or not a reviewer is selected. Changing them together, as #865 did, makes every
open cover consideration need a refresh:

- an owner cover decision made against the earlier consideration no longer
  resolves the current interaction;
- automatic cover selections bound to the earlier replay digest are retired
  before they execute, and none executes under the earlier identity;
- a consideration is prepared again when a scan reaches its case or the owner
  prepares it, through the existing preparation path. With a reviewer selected, that is one reviewer request per
  re-prepared consideration at the selected model's price; with none, no
  request is made.

### Observations stay advisory

No candidate admission, exclusion, order or election, no ADR-0093 stage rule,
no ADR-0095 builder or judge input, no attention count and no automatic gate
reads an observation value. The replay identity above binds observation
contracts for currentness only. A dimension can gain authority only by qualifying under
#445 on fresh owner labels; the 2026-10-24 holdout is spent.

The three `cover-policy-v1` booleans (avoid promotional text, avoid tie-in
branding, prefer minimal composition) stay recorded and inert, as today. What
they mean against the revised values is decided by whichever later policy
qualifies a dimension.

### Display

The defect summary reads `cover_image_kind` (either value other than
`front_cover`), `reproduction`, `overlay_marking`, and `format_branding`
against the item's format. As today, only a complete row with every defect check
clean may say that no defect was flagged. The other dimensions are style
notes. The implementation contract quotes the labels.

### Reviewer posture

The stable cover reviewer posture (#1124, ADR-0062) is unchanged: the exact
selected model, provider data collection denied, retention permitted, the
8,192-token ceiling, minimal excluded reasoning and strict structured output.
The packet is unchanged, so there is no new disclosure.

### Before activation

Activation is the implementation pull request that makes v4 the current
instruction. Two owner-run steps come first, recorded on #1417:

- a live smoke of the v4 schema on the owner's selected reviewer model, under
  an exact preflight naming the model, request count and spending bound before
  any request. This is the new qualification ADR-0046 requires for redefined
  labels, at the level an advisory observation needs: valid, bound reports,
  not accuracy;
- fresh owner labels, starting with a targeted batch of rare positives
  (tie-in, non-English text, placeholder, prominent promotion), before any
  dimension is put forward for qualification.

A model that cannot return the v4 schema leaves the model-free manual path.
There is no parser healing, retry until pass or silent model substitution.

## Consequences

### Positive

- The observer can tell minor from prominent promotion, a tie-in sticker from
  tie-in art, and a placeholder or non-cover image from a front cover, which
  is what fresh labels need to measure.
- Dimensions that measured the preview instead of the cover leave the
  vocabulary.

### Negative

- Earlier observations become history, and every open cover consideration
  needs a refresh (see the replay identity above): earlier owner cover
  decisions on open cases no longer resolve the current interaction, pending
  automatic cover selections are retired, and with a reviewer selected each re-prepared
  consideration costs one reviewer request.
- Text-reading dimensions (promotion, platform banner, tie-in, non-English
  text) rely more on reading printed words, which is where hostile text sits.
  The closed values bound the effect to one advisory value per dimension.

### Neutral or deferred

- The report and output versions change. Retained-history decoding follows
  #865's precedent. If the implementation changes a persisted schema version,
  it declares a clean rebuild under ADR-0013.
- Observation values remain closed. No raw model output, preview, prompt or
  title reaches a log line, diagnostic, status or support bundle; failures are
  closed codes.
- Excluded: printed cover text (#1354), placeholder fingerprints (#1333), the
  taste judge (#1372) and any rule that reads an observation.
- On acceptance, `security.md` › Cover observer, `ai-boundary.md`,
  `contributor-workbench.md`, ADR-0062's recommendation note if the
  recommended model changes, and the changelog change in the implementation
  pull request.

## Alternatives considered

### Keep the twelve dimensions and qualify them as they are

Presence of promotion cannot rank, tie-in mixes two behaviours the owner
treats differently, and placeholders were never detected. Fresh labels on the
old vocabulary would measure the same confusions.

### Use a dimension as a veto now

None met the bar on the labelled set; the closest was explained by shape,
which pixels already decide.

### Add free-text notes

A free-text field would carry hostile printed text into review and break the
closed schema every consumer relies on.

## Validation

- Domain tests: every closed value admits; an out-of-vocabulary value, a
  missing or extra dimension, a duplicate or invented candidate reference and
  a dropped dimension (`medium`, `thumbnail_legibility`) are each rejected.
- A v1, v2 and v3 report each decode as history against their own taxonomy,
  output schema and instruction, keep their observer reference and model
  label, and are refused as current.
- The instruction's byte length is asserted at or below 4,096. The
  instruction and taxonomy digests are pinned, and the output schema digest is
  pinned for a fixture candidate set, since each request's schema lists its own
  candidates.
- Tests assert that the election, ADR-0093 stage, ADR-0095 builder and judge
  packets and the automatic gate read no observation value: changing every
  observation value leaves their outputs unchanged.
- An application test shows the v4 change marks an open consideration as
  needing a refresh, retires a pending automatic cover selection bound to the
  earlier digest, and makes no reviewer request when none is selected.
- Browser tests: the defect summary for each defect value, an incomplete row
  shown as unavailable, and history labelled with its model.
- A synthetic cover with instruction-like printed text yields only closed
  values or a rejected report.
- The owner-run live smoke on the selected reviewer model, before activation.
