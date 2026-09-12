# ADR-0075: Independently supported ebook title corrections

- Status: accepted
- Date: 2026-09-11
- Issue: #1007

## Context

A useful work title can be supported by a retained ISBN lookup and positive
creator evidence even when package or target text contains an arbitrary title.
Requiring subtitle and series decisions first prevents that independent repair.

## Decision

For ebooks, an existing provider title may be elected when a successful retained
ISBN lane binds it to a valid local ISBN associated with the book, positive
creator evidence identifies the work, and no material competing work, author,
title or variant claim remains. One independent provider is sufficient. Several
same-work print and ebook ISBNs are permitted; duplicate records do not add
authority. Provider failure and missing evidence are not invented dissent.

This replacement option requires a populated target title; filling an empty
title retains its existing policy. Complete package and provider author lists
use the existing ordered lexical comparison, preserving cardinality and duplicate
rejection. Populated target Authors can veto a conflict but never supply positive
identity authority. Opening-matter author occurrences are separate observations:
each explicit name must match a supported author, while repeated observations
add no authority. Opening observations alone cannot establish a complete list.

The exact elected candidate carries a closed `independent_title` fact in packet
v7. Only that candidate can compile and reach a title-only deterministic action
under action policy v2 and decision policy v12. Other alternatives remain visible;
meaningful source associations remain intact. No preserve-companion combination
is fabricated. This narrowly supersedes ADR-0021's universal combination
requirement and amends ADR-0022's candidate summary contract. All other elections
and coupled actions retain their existing rules.

Current title qualification admits the reason-specific
`title:replace_target:isbn_creator_supported` option. Pre-option historical manifests do
not acquire it. Published v11 remains readable with its original option set, but
only the current v15 qualification grants current authority. Ask, Ignore, owner-final decisions, pause and title-layout
suspension remain authoritative. Consent, backup, currentness, read-back, audit
and recovery remain prerequisites for any guarded effect. Reviewers remain
advisory and receive no write or credential authority.

Explicit language-script disagreement remains material alongside differing
language identities. The closed base-and-script vocabulary supplies explicit
script identity; regional variation and an unspecified script add no dissent.
When providers retain no valid title, this election preserves the earlier title
result instead of comparing local content against an invented empty title.

## Consequences

**Clean rebuild required** under ADR-0013. Proposal policy v24 changes the exact
store DDL; proposal document v7, report v40 and relationship v1 remain unchanged.
Packet-v6 and predecessor stores reject without mutation. No adoption or bridge
refresh is supplied.

Qualification reports candidate reachability separately from durable decisions
and guarded effects, using two independently labelled retained positives and a
finite synthetic matrix. Historical measurements remain historical. Current F20
continuity is narrower: all thirteen options through synthetic persisted effects
and two retained current reports across those options. No population accuracy or
full-library attention-saving claim follows from these measurements.
