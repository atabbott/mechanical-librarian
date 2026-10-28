---
name: Mechanical Librarian
description: A calm editorial workspace for evidence-first library curation.
colors:
  reading-room-paper: "#f4f2ec"
  porcelain-sheet: "#fbfaf6"
  bright-sheet: "#ffffff"
  inkwell: "#17201e"
  quiet-ink: "#56615e"
  archive-teal: "#1b6558"
  archive-teal-deep: "#0f4c42"
  archive-teal-wash: "#dcebe6"
  catalogue-line: "#d9d7cf"
  catalogue-line-strong: "#b9beb9"
  margin-ochre: "#8a5a16"
  margin-ochre-wash: "#f4e7cf"
  correction-red: "#8c3434"
  focus-blue: "#146fba"
  after-hours-paper: "#0f1512"
  deep-stacks: "#151d19"
  raised-night-sheet: "#1b2520"
  night-ink: "#edf2ee"
  night-quiet-ink: "#a9b6b0"
  lamplight-teal: "#61b29f"
  lamplight-teal-bright: "#8ed2c1"
  lamplight-teal-wash: "#203b34"
  night-line: "#2d3933"
  night-line-strong: "#4a5a52"
  gilded-note: "#efbd70"
  gilded-note-wash: "#3b2d1b"
  night-correction-red: "#f39b9b"
  night-focus-blue: "#78bdf2"
typography:
  display:
    fontFamily: "Georgia, Times New Roman, serif"
    fontSize: "clamp(1.65rem, 3vw, 2.5rem)"
    fontWeight: 700
    lineHeight: 1.12
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Georgia, Times New Roman, serif"
    fontSize: "1.45rem"
    fontWeight: 700
    lineHeight: 1.2
  title:
    fontFamily: "Georgia, Times New Roman, serif"
    fontSize: "0.95rem"
    fontWeight: 700
    lineHeight: 1.3
  body:
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.78rem"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 800
    lineHeight: 1.2
    letterSpacing: "0.08em"
rounded:
  field: "0.45rem"
  control: "0.5rem"
  row: "0.65rem"
  choice: "0.7rem"
  panel: "0.75rem"
  surface: "1rem"
  pill: "999px"
spacing:
  tight: "0.25rem"
  xs: "0.35rem"
  sm: "0.5rem"
  md: "0.75rem"
  lg: "1rem"
  xl: "1.25rem"
  page: "2.6rem"
components:
  button-default:
    backgroundColor: "{colors.porcelain-sheet}"
    textColor: "{colors.inkwell}"
    rounded: "{rounded.control}"
    padding: "0.6rem 0.85rem"
    height: "2.75rem"
  button-primary:
    backgroundColor: "{colors.archive-teal-deep}"
    textColor: "{colors.porcelain-sheet}"
    rounded: "{rounded.control}"
    padding: "0.6rem 0.85rem"
    height: "2.75rem"
  button-danger:
    backgroundColor: "transparent"
    textColor: "{colors.correction-red}"
    rounded: "{rounded.control}"
    padding: "0.6rem 0.85rem"
    height: "2.75rem"
  status-pill:
    backgroundColor: "{colors.catalogue-line}"
    textColor: "{colors.quiet-ink}"
    rounded: "{rounded.pill}"
    padding: "0.18rem 0.45rem"
  navigation-active:
    backgroundColor: "{colors.archive-teal-wash}"
    textColor: "{colors.archive-teal-deep}"
    rounded: "{rounded.pill}"
    padding: "0.65rem 0.8rem"
    height: "2.75rem"
  input:
    backgroundColor: "{colors.bright-sheet}"
    textColor: "{colors.inkwell}"
    rounded: "{rounded.field}"
    padding: "0.7rem"
    height: "2.75rem"
  card:
    backgroundColor: "{colors.porcelain-sheet}"
    textColor: "{colors.inkwell}"
    rounded: "{rounded.panel}"
    padding: "1rem"
---

# Design System: Mechanical Librarian

## Overview

**Creative North Star: "The Conservator's Reading Room"**

Mechanical Librarian feels like a carefully kept private reading room where
every book, note, and decision has a precise place. The interface is calm,
exact, editorial, and quietly domestic: warm paper planes, green-black ink,
restrained archive teal, and serif bibliographic emphasis make operational work
feel considered without disguising its technical authority.

The system remains dense enough for serious review, but hierarchy comes from
typography, spacing, borders, and tonal layers rather than dashboard spectacle.
Glossy SaaS chrome, playful consumer-library styling, decorative gradients, and
attention-seeking motion are anti-references.

**Key Characteristics:**

- Warm paper surfaces with a semantic, fully paired dark theme.
- Editorial serif emphasis inside a compact sans-serif operating system.
- Thin catalogue-like rules, restrained controls, and rounded reading surfaces.
- Archive teal for current selection and affirmative action; ochre and red for
  exceptional states.
- Responsive task continuity from wide workspaces to a 320px minimum viewport.

## Colors

The palette treats color as notation on paper: teal marks the current or
affirmative path, ochre annotates consequential attention, and red is reserved
for refusal, failure, or revocation.

### Primary

- **Archive Teal** (`#1b6558`): the light-theme accent for selected borders and
  active state emphasis.
- **Deep Archive Teal** (`#0f4c42`): readable active text, links, and primary
  action fills.
- **Archive Teal Wash** (`#dcebe6`): selected navigation, active rows, and calm
  success or decision backgrounds.
- **Lamplight Teal** (`#61b29f`), **Bright Lamplight Teal** (`#8ed2c1`), and
  **Lamplight Teal Wash** (`#203b34`): dark-theme equivalents that retain the
  same semantic hierarchy.

### Secondary

- **Margin Ochre** (`#8a5a16`) and **Margin Ochre Wash** (`#f4e7cf`): notices,
  review totals, recovery, and caution in the light theme.
- **Gilded Note** (`#efbd70`) and **Gilded Note Wash** (`#3b2d1b`): the paired
  dark-theme notice colors.

### Tertiary

- **Correction Red** (`#8c3434`) and **Night Correction Red** (`#f39b9b`):
  errors, destructive or revocation actions, and blocked status.
- **Focus Blue** (`#146fba`) and **Night Focus Blue** (`#78bdf2`): keyboard
  focus only; this blue is an accessibility signal, not a decorative accent.
- **Correction Wash** (`--danger-soft`): derived, not a new hue. It is 10%
  Correction Red over Porcelain Sheet or Deep Stacks, so each theme resolves its
  own pair, and it backs danger status pills, stopped preparation rows, clear
  confirmations, and read failures.

### Neutral

- **Reading Room Paper** (`#f4f2ec`): the light page canvas.
- **Porcelain Sheet** (`#fbfaf6`) and **Bright Sheet** (`#ffffff`): nested
  light-theme surfaces and stronger fields.
- **Inkwell** (`#17201e`) and **Quiet Ink** (`#56615e`): primary and supporting
  light-theme text.
- **Catalogue Line** (`#d9d7cf`) and **Strong Catalogue Line** (`#b9beb9`):
  dividers and container edges. Neither is a field boundary: against the
  surfaces fields sit on they fall short of 3:1.
- **Field Line** (`--field-line`): derived, not a new hue. It is 64% Strong
  Catalogue Line toward Inkwell (`#7f8581`), or Strong Night Line toward Night
  Ink (`#85918a`), and holds 3:1 on every surface. It is the border of every
  text field, search box, and select.
- **After-Hours Paper** (`#0f1512`), **Deep Stacks** (`#151d19`), and **Raised
  Night Sheet** (`#1b2520`): dark-theme canvas and surface sequence.
- **Night Ink** (`#edf2ee`) and **Night Quiet Ink** (`#a9b6b0`): dark-theme text.
- **Night Line** (`#2d3933`) and **Strong Night Line** (`#4a5a52`): dark-theme
  structural boundaries.

### Named Rules

**The Marginalia Rule.** Color annotates state; it does not decorate empty
space. Archive teal identifies the current or affirmative path, ochre calls for
care, and red is reserved for failure or destructive consequence.

**The Semantic Pairing Rule.** Light and dark themes preserve role and hierarchy,
not literal brightness relationships. Never substitute an unpaired hue when
switching themes.

## Typography

**Display Font:** Georgia (with Times New Roman and serif fallbacks)

**Body Font:** Inter (with system sans-serif fallbacks). The review workspace
self-hosts Inter's variable latin and latin-ext faces (SIL Open Font License),
swapped in when loaded; sign-in and setup still render the fallback stack.

**Character:** Georgia gives titles, work identity, values, and metrics the
cadence of a catalogue or reference edition. Inter carries navigation, labels,
controls, diagnostics, and operational copy with compact precision.

### Hierarchy

- **Display** (700, `clamp(1.65rem, 3vw, 2.5rem)`, 1.12): page identity, with
  `-0.025em` tracking so large headings remain composed.
- **Headline** (700, approximately `1.25rem`–`1.45rem`, 1.2): section and work
  headings that need bibliographic emphasis.
- **Title** (700, approximately `0.9rem`–`1.05rem`): work titles, field values,
  and compact editorial emphasis.
- **Body** (400, commonly `0.75rem`–`0.82rem`, 1.45–1.55): bounded operational
  explanation; introductory copy stays near 68–72 characters per line.
- **Label** (750–800, `0.75rem`, up to `0.08em`, uppercase when used as an
  eyebrow): state, category, and compact navigation language.

### Named Rules

**The Catalogue Voice Rule.** Use serif type for bibliographic identity,
decision values, and measured emphasis. Use sans-serif type for everything the
owner must operate, scan, or verify.

**The Type Floor Rule.** No text is set below `0.75rem` (12px at the default
size), including labels, counts, marks, and compact layouts; density comes from
weight, spacing, and line length, never smaller type. Sizes are in `rem` so they
follow the owner's text size; only the fixed-size masthead mark caps its
initials at 14px.

## Layout

The application is a bounded editorial workspace rather than an edge-to-edge
dashboard. Main content is capped at `98rem` and padded with
`clamp(1rem, 3vw, 2.6rem)`. A sticky 62px masthead holds identity, four permanent
destinations—Library, Policy, Activity, and Settings—plus the live-update
status (shown only while it reconnects), preparation status, theme, and sign-out
controls. Attention stays contextual to Library instead of competing as a
permanent destination.

Wide task surfaces use asymmetric grids. Library pairs a sticky 310–380px work
index with a flexible preview; Policy pairs a 220px chapter index with notebook
pages. Activity instead stacks a status and owner-authority strip above its
full-width ledger. The common rhythm is compact—`0.25rem`, `0.35rem`, `0.5rem`,
`0.75rem`, `1rem`, and `1.25rem`—with larger space reserved for page framing
rather than individual controls.

Width breakpoints follow one ladder: 1200px, 1024px, 760px, and 390px, with a
320px floor. Every width query uses one of those steps, written as
`max-width: 1199px` below 1200 and as `min-width` 1025px, 761px, or 391px for the
range above a step. Below 1200px, detail facts move below the field heading,
Activity ledger columns tighten, and from 761px attention becomes a contextual
Library drawer. At 1024px, workspace columns tighten, selected Library work
replaces the index with an explicit back control, and cover comparisons and
direct choices fold into one scrollable or stacked row. At 760px, grids become
single-column, sticky side regions become inline or compact pickers, the
masthead wraps into a horizontally scrollable destination row, and page padding
becomes `1rem 0.75rem 4rem`. Focused refinements at 390px preserve controls and
ledger reading order. Nothing assumes a viewport wider than 320px.

## Elevation & Depth

The system is flat by default. Depth comes first from paper-toned surface
changes and one-pixel rules. Ambient shadow is reserved for genuinely floating
or consequential layers: the preparation popover, sticky Policy and Settings
finish trays, Policy notebook, dialogs, focused skip link, and compact sticky
Policy picker.
Translucent sticky surfaces use a restrained 12px backdrop blur.

### Shadow Vocabulary

- **Reading-Room Lift** (`0 16px 46px rgb(30 40 37 / 0.08)`): light-theme
  floating surfaces and consequential overlays.
- **After-Hours Lift** (`0 18px 52px rgb(0 0 0 / 0.28)`): dark-theme equivalent.
- **Compact Index Lift** (`0 8px 18px rgb(0 0 0 / 0.08)`): mobile sticky Policy
  chapter picker only.

### Named Rules

**The Borders-Before-Shadows Rule.** Establish grouping with tone and a
one-pixel border. Add shadow only when a surface actually floats, overlaps, or
must remain consequential while content moves beneath it.

## Shapes

Shapes are softly editorial rather than bubbly. Fields and ordinary buttons use
approximately `0.45rem`–`0.5rem` corners; rows and direct choices use
`0.65rem`–`0.7rem`; panels use `0.75rem`; major Library and Policy surfaces use
`1rem`. Pills (`999px`) are reserved for navigation, lenses, compact state
chips, and count summaries—not general containers.

The signature book mark uses an asymmetric `2px 5px 5px 2px` silhouette so a
synthetic initial block reads as a book spine without requiring cover imagery.
Its initials are the first letter of each of the first two words that have one,
so punctuation never stands for a word.
In the dark theme every mark (masthead, Library, and Policy) shares one spine,
`#315c52`, so its paper-colored initial keeps 4.5:1.
Dashed borders distinguish current-value choices; left rules mark notices,
status, recovery, and current ledger events.

## Components

Components feel restrained and tactile: clear enough to operate quickly,
substantial enough to trust, and never ornamental for their own sake.

### Buttons

One `Button` carries three variants: primary, secondary, and danger. It sets no
default type, so every call site states its own.

- **Shape:** compact rounded rectangle (`0.5rem`) with `0.6rem 0.85rem` default
  padding. Primary task and touch targets are at least `2.75rem`/44px; dense
  desktop inspection and pagination controls may use 40px before returning to
  44px at compact widths. Disclosure summaries and owner-authority controls
  take no dense exception: they are 44px at every width.
- **Primary link:** a link styled as the primary action takes the primary
  button's type, weight, and 44px target, without an underline, so the two
  read as one control.
- **Primary:** Deep Archive Teal fill with a paper-colored label; use for the
  next authoritative action, not every available action.
- **Hover / Focus:** hover shifts borders or surfaces toward Archive Teal and
  its wash. The default keyboard focus treatment is a three-pixel Focus Blue
  outline, unmixed so it holds 3:1 on every surface, with a three-pixel
  offset. Compact native fields may use the implemented two-pixel Archive Teal
  outline where their local component contract requires it.
- **Secondary / Ghost:** paper-toned or transparent surfaces with catalogue
  borders; secondary is the plain button.
- **Danger:** transparent with Correction Red text and border until explicitly
  invoked.
- **Disabled:** every variant keeps its structure at 55% opacity with a
  not-allowed cursor. A command marked `aria-disabled` while it runs looks the
  same but keeps keyboard focus, so focus never falls to the page. Rows and
  pagers kept on screen while a read is pending are `aria-disabled` inside
  busy content and stay at full opacity, because the busy mark, not dimming,
  says the list is updating.

### Chips and Status Pills

- **Style:** fully rounded, compact, and text-led. Lenses have a catalogue-line
  border.
- **Status pills:** one `StatusPill` describes compact state in four tones:
  neutral (Quiet Ink on a softly mixed line background), notice (Margin Ochre
  on its wash), accent (Deep Archive Teal on Archive Teal Wash), and danger
  (Correction Red on Correction Wash). The words carry the state; the tone only
  reinforces it. Counts are not status and keep their own badges.
- **State:** selected chips use Archive Teal borders, Deep Archive Teal text,
  and Archive Teal Wash. Chips filter or describe state; they do not become
  miniature cards.

### Cards / Containers

- **Corner Style:** `0.75rem` for ordinary panels and `1rem` for major reading
  surfaces.
- **Background:** Porcelain Sheet on Reading Room Paper; Bright Sheet marks
  stronger nested fields. Dark mode uses Deep Stacks and Raised Night Sheet.
- **Shadow Strategy:** flat unless the surface overlaps or floats.
- **Border:** one-pixel Catalogue Line, strengthened only for fields, choices,
  or consequential surfaces.
- **Internal Padding:** usually `1rem`, rising to responsive
  `clamp(1rem, 3vw, 2rem)` for ledger-scale regions.

### Inputs / Fields

- **Style:** Bright Sheet, Field Line, `0.45rem` corners, and a minimum 44px
  control height where explicitly sized.
- **Focus:** the global Focus Blue outline remains visible outside the field;
  focus is never communicated by color fill alone.
- **Error / Disabled:** error text and boundaries use Correction Red. Disabled
  controls retain structure at 55% opacity and use a not-allowed cursor.

### Navigation

The sticky masthead uses quiet, bold sans-serif labels in 44px pill targets.
The active destination alone receives the Archive Teal Wash. At compact widths,
the product name and secondary shell labels recede while Library, Policy,
Activity, and Settings remain visible in a horizontally scrollable row. Attention is opened
from Library as a contextual queue, not represented as permanent navigation.

### Work Row and Book Mark

Library rows combine an asymmetric initialled book mark, a serif work title,
quiet bibliographic metadata, compact status, and an optional preview action.
Hover uses a subtle page/surface mix; selection adds an Archive Teal boundary
and wash. The mark is identity support, never a fake cover. When a work's report
retains the cover its library holds, that real cover takes the mark's place in
the row and the open work's header: contained whole, at most 60 by 90px, and
never setting the row's height. Only that work's row widens its mark column for
it, so no other row's text loses width; below 760px the cover fits the spine's
own 42px column, at most 63px tall. Every other work keeps its spine, level with
the cover's left edge, and a cover that fails to load falls back to it.

### Direct Value Choice

Field decisions are large, left-aligned surface buttons with a minimum height of
`9.5rem`, Strong Catalogue Line, `0.7rem` corners, and serif values. A field's
choices are one radio group: each states its state with a 20px ring in the
field-line colour, which hover previews with a dot. The recorded choice fills
the ring, takes the Archive Teal Wash with a 2px inner accent edge, and says
"Your choice" in words; a recommendation is stated in its label, not by a wash.
Keyboard focus is a 3px focus-colour outline drawn inside the choice. A dashed
edge means the current value; automatic, resolved, or stale fields must not
present a false choice. Field tabs name their status in words (Needs you,
Decided, Automatic, Optional, No change), never by a glyph.

### Cover Choice

Cover candidates join the same radio model and record in one tap: a card that
says "Match not confirmed" is itself the explicit choice, with no confirming
dialog. Each card states its pixel size and every source that offered the image
on their own lines, then its rationale; one image offered by several sources is
one card naming them all, and the merged count is said apart from unavailable
candidates. Off-screen cover images load lazily.

### Cover Observation Evidence

Whole-cover choice remains primary. Each candidate places one compact visual-
check block after its decision button, so the block cannot become or intercept
the choice control. High-signal defects are always first: a complete all-clear,
an explicit issue count with plain-language flags, an uncertainty state, or an
unavailable-evidence state. Unavailable reviewer evidence never uses the all-
clear treatment.

The exact defect checks and the quieter style/advisory observations live in one
native disclosure below that summary, with defects before preferences in
document order. Existing source evidence stays a separate reachable disclosure;
reviewer observations do not replace deterministic match evidence or source
provenance. At compact widths both disclosures keep 44px targets and travel with
their cover in the same horizontal comparison track. Color reinforces the
written state but never carries it alone.

### Policy Notebook and Activity Ledger

Policy uses a chapter-indexed notebook with restrained form controls and a
sticky impact tray so validation, preview, and activation remain one continuous
decision. Activity uses a denser ruled ledger: state and owner authority appear
before filterable event rows, and expanded evidence stays attached to its
event. At compact widths both surfaces preserve document order rather than
compressing their desktop columns.

Settings shares the notebook's controls rather than restyling copies:
segmented Off/On choices, dropdowns, native disclosures, change-count badges
and the inset finish tray are the same components on both pages. Each page
keeps its own draft, validation and save.

## Do's and Don'ts

### Do:

- **Do** use warm paper, ink, and catalogue lines as the default hierarchy.
- **Do** reserve Archive Teal for current selection, affirmative action, and
  calm recorded state.
- **Do** keep primary task and touch targets at least 44px high. Dense desktop
  inspection controls may use 40px when they return to 44px at compact widths;
  disclosure summaries and authority controls never do. Preserve the default
  three-pixel offset focus outline and documented compact field exceptions.
- **Do** keep task identity and direct values in serif type while operational
  controls and evidence remain sans-serif.
- **Do** collapse wide workspaces into explicit, continuous mobile tasks rather
  than squeezing desktop columns.
- **Do** honor `prefers-reduced-motion`; state changes must remain understandable
  with animation effectively removed.

### Don't:

- **Don't** use color as ambient decoration or introduce an unpaired theme hue.
- **Don't** add shadow to a surface that does not float, overlap, or remain
  consequential while scrolling.
- **Don't** turn every control or container into a pill; pills are reserved for
  navigation, lenses, and compact state.
- **Don't** imitate a glossy analytics dashboard, playful bookshelf app, or
  cover-led streaming catalogue.
- **Don't** fabricate cover art where the interface only has initials or target
  identity.
- **Don't** add attention-seeking motion; the incumbent system uses motion only
  as a reducible state transition.
