# ADR-0104: Owner identity links

- Status: proposed
- Date: 2026-11-16
- Owners: project maintainers
- Issues: #1477, #1937
- Amends: ADR-0088 (a standing link supersedes the carried ASIN), ADR-0013
  (one more clean rebuild)
- Relates to: ADR-0092, ADR-0096, ADR-0101, ADR-0102
- Supersedes: none
- Superseded by: none

## Context

Some works never reach their book. Junk titles, missing authors or a
title shared by many books leave every search empty or ambiguous, so the work
stays Not matched, or the record a source returns is refused. The owner often
knows which record is the book. Nothing could record that knowledge, so each
preparation searched again from the same weak local values.

A witness id stored by preparation is not this. Preparation found it, so it
proves only that preparation found it. A record the owner chose is a statement
about the work, and it should steer what preparation reads.

## Decision

1. **Authority and scopes.** An owner action links an item (connection,
   library, item ID) to one source record as *this book* (a work link), *this
   edition* or *this recording*. Each item has at most one current link. Rows
   in `owner_links` are append-only (link, replace, withdraw): update and
   delete abort, a trigger enforces the sequence, and the row is its own audit
   record. `source` is a closed set, `search` today. `record_owner_link`
   writes a row inside the caller's write transaction and marks the work
   fresh in the same transaction, so the next scan prepares it again. It
   takes no import ID. A link grants no write authority. It never fills an
   identifier field and never withdraws an owner decision.
2. **Owner search.** The owner finds the record by searching one source with
   that source's existing request shape. Results are never evidence and are
   never stored. The search and its routes come in the owner-facing
   increment. This decision covers the link and what preparation does with it.
3. **Direct lookups.** Preparation reads the linked record by its key before
   any search, on a `link` lane, under the provider session's usual limits:
   - Hardcover: the constant `BookById` document.
   - Open Library: the work or edition key, with at most four author reads for
     creator names.
   - Inventaire: one entity read by URI.
   - An edition link is asked as its ISBN, and a recording link as its ASIN,
     on each edition or recording source's identifier lane.

   A record that is absent is `missing`. A redirect or a `canonical_id` naming
   another record is `merged`. The pointer is never followed
   ([provider egress](../provider-egress.md#owner-links)).
4. **Admission `owner_link`.** A record the link lane returns is admitted on
   the link: it names the identity at every Book matching level. An edition
   link is re-checked at every preparation against the format the record
   states. A recording link is re-checked with ADR-0088's variant, runtime band
   and narrator gates, without exact work admission. Its runtime must agree
   positively: a product that states no runtime, or a file that states none,
   does not confirm the link, and it reads `contradicted` with reason
   `runtime`. That keeps the closed outcome set; "not confirmed" and
   "disagrees" both leave the owner to look again. A record that fails is
   dropped and the link reads `contradicted` with its reason (`format`,
   `variant`, `runtime` or `narrators`). The report's `owner_link` facts record
   medium, scope, provider, identity, outcome, reason and anchor, bound by the
   link's digest. Outcomes are `admitted`, `contradicted`, `unavailable`,
   `merged`, `missing` and `source_disabled`, in that precedence, and
   `medium_changed` for a link made for the other medium, which reads nothing.
   Whether a record was kept through the link is derived from the report's
   lanes (`owner_link_admission`), never stored.
5. **Anchor `owner_link_anchor`.** With an admitted link, the linked record's
   work title and creators, as read in the same preparation, are the anchor.
   The other sources get one anchored WORK lane in place of the asserted and
   alternative WORK lanes. Every title and creator comparison reads the anchor
   instead of the local values, so a record that matches only the local values
   is not admitted. While a link stands but no anchor was read (`unavailable`,
   `missing`, `merged`, `contradicted`, `source_disabled`, or an admitted
   record that states no complete creator set), no WORK lane is planned and
   no lane admits against any title or creators: the local values never
   admit a record while the owner's link stands. That includes an
   identifier lane's alternative routes, the item's discovery titles and its
   companion EPUB's title and work, whether or not an anchor was read. The report replays each
   admission against the same context, the anchor while the link stands. An
   item whose own title surfaces are all empty gets no anchored WORK lane,
   because the lane's inputs are the target's own title claims; a link
   claim carries no work title to stand it on. This is a known limit of
   increment 1. **Every link steers: work, edition and recording links all anchor.
   This is the owner's choice** (#1477, answering the planning question).
   Otherwise local-only matches would compete with an edition or recording the
   owner confirmed. One predicate, `owner_link_anchors`, holds the choice.
6. **ADR-0088 amendment.** Any standing audiobook link supersedes the carried
   ASIN, a work or edition link as well as a recording link. Under a work
   link a carried product would be re-admitted on the local values, which
   never admit while the link stands (maintainer ruling on #1937). Clause 1
   carries nothing while one stands, and clause 3's carried lane is not
   planned. Clause 4's variant, band and narrator gates are reused for the
   recording link without exact work admission. An ASIN the item's own
   surfaces assert is still asked.
7. **Shadow-first, until #1479.** Link and anchor evidence reaches proposals in
   the live set, so it works at `identifier` too. It decides nothing
   automatically:
   - In the shadow decision, a candidate whose every cited record was kept
     only through the link or its anchor is ineligible. The decision is
     manual with the closed reason `owner_link_unqualified`. It carries
     ADR-0092's would-admit note, the alternative the ungated engine selects,
     and the shadow outcome records the note under the rule `owner_link`.
   - A cover all of whose records were kept only through the link or its
     anchor is at most `plausible_match`, with reason `owner_link_unqualified`.
     So no ADR-0093 switch and no ADR-0095 automatic mode can bind it.
   - Automatic description admission (ADR-0099) abstains for a report that
     holds such a record.
   - ISBN and ASIN proposals never cite such a record. Identifier fields keep
     their own rules.

   A record counts as kept through the link or its anchor
   (`owner_link_reached`) when every lane holding it is the link lane, the
   anchored WORK lane, or an ASIN lane fed only by ASINs those lanes
   promoted. It also counts when the anchor is all that admits it: its ASIN
   lane admission replays against the anchor but not against the target's
   own values, or it represents the work only against the anchor. The audio
   editions the linked Hardcover book lists are leads like a search's: they
   feed the per-ASIN sources, and what those return is kept through the
   link.
8. **Preparation and recovery.** The report binds the link digest, so a link
   change gives a new report and case on the next preparation. A failed link
   lane enrolls in provider recovery like any lane. The successor rebuilds it
   from the source report's own `owner_link` facts and retries it alone and
   first. A main lane is retried under the anchored plan. When a successor
   newly admits the link, its publication marks the work fresh and requests a
   scan, so the next ordinary preparation runs the anchor. No cross-provider
   fan-out runs inside the successor. Stored answers are reused as usual
   (ADR-0101). A timed re-search (ADR-0102) of a linked work uses the anchor.
9. **Privacy and logging.** Operational events gain only the closed phase and
   lane kind `link`. No identity, title, creator or query reaches a log, event
   or diagnostic
   ([data privacy](../data-privacy.md#what-is-retained)).
10. **Versions; clean rebuild.** `metadata-report-v82` adds `owner_link`.
    `fixed-conservative-v48` covers the identifier rule.
    `deterministic-shadow-decision-policy-v28`, `shadow-review-packet-v10`
    (the candidate summary's `owner_link_only`) and `shadow-outcome-set-v12`
    cover the gate and its note. `cover-identity-report-v3` covers the cover
    cap. The application database gains `owner_links` and
    `current_owner_links`. Every earlier application database is rejected
    before mutation and needs a clean rebuild
    ([ADR-0013](0013-pre-release-clean-schema.md)).

## Consequences

- A linked work is read from the owner's record first, and every other source
  is asked about that book rather than about the file's local values.
- Nothing a link admits writes anything on its own until #1479 qualifies the
  link rules. Until then the owner decides, and the shadow record shows what
  would have been decided.
- The cover identity outcomes `owner_link_match` and `owner_link_anchor_match`,
  which the plan named, are not added yet. They reach the review wire and the
  human decision document, so they come with the owner-facing increment. Until
  then a link-only cover is capped at `plausible_match`.
- Increment 1 changes nothing the owner sees: no route, no Library, History or
  Activity change, and no response-shape version moves. The link lane kind is
  kept off the review wire's lane vocabulary until the owner-facing increment
  can record a link.

## Alternatives considered

- **A level-set carrier like ADR-0096's.** Rejected: it is invisible at
  `identifier`, where most owners stay.
- **Following merge pointers.** Rejected: a merged record is another record
  the owner did not choose.
- **Storing owner search answers.** Rejected: they are not evidence, and
  keeping them would retain the owner's queries.
- **Anchoring work links only.** Rejected by the owner (decision 5).
