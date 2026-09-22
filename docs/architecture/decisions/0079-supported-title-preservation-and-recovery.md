# ADR-0079: Supported title preservation and explicit recovery

Status: accepted

## Context

An ordinary evidence-supported unchanged title can have citations and no
bibliographic combination. Treating that absence as malformed mutation authority
suspended the whole title category and left completed preparation unclaimable
(#1073).

## Decision

A closed ordinary title witness binds the exact source outcome and citations for
`title_layout` preserve/title-and-subtitle, `keep_current`, no candidate, empty
combinations and `unique_supported_preservation`. It authorizes only unchanged
value. Audiobook witness identities stay unchanged; changing titles still require
their existing authority. Protected coherence checks apply only to destinations
actually routed to Automatic. Ask and Ignore never trigger a suspension.

The existing owner recovery command reads safety and removes the requested
suspension in one SQLite transaction. Removing an existing title suspension also
requeues eligible completed title projections through the preparation owner on
that same connection. Eligibility requires current report, file, proposal and
case lineage, current policy generation/resolution, an Automatic title outcome,
and no final title decision or active manual hold. Running and queued work stay
under their current owner. No acquisition, extraction or model work is queued.
Terminal scans and preparation presentation refresh in the same transaction.

SQLite serializes recovery with completion, report publication, policy activation
and owner decisions. Claim/projection currentness and final decision uniqueness
remain authoritative after recovery; later drift follows existing stale handling.
Pause, global and unrelated category suspensions remain effective. This is local
transactional consistency, not perpetual or cross-system atomicity.

## Consequences

The existing scheduler records one durable no-change fact before target guards;
it creates no apply plan, consent, backup check or target call. Repeated recovery
without another suspension creates no cohort. Any transaction failure retains
both suspension and completed work.

Shadow outcome v7 and F19a v32/F20 v46/F46 v15 require a **clean rebuild** of
predecessor application stores under ADR-0013. Existing v6 stores remain untouched;
the recovery control is not a migration. Historical qualification documents remain
readable as history and grant no current authority. Report v47, proposal policy
v28, action policy v4 and decision policy v15 remain unchanged.
