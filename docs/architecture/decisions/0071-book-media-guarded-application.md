# ADR-0071: Guarded application for book-media destinations

- Status: accepted
- Date: 2026-09-04
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

ADR-0039 bound per-library consent to "the eligible subset of the existing 14
ebook destinations", because when it was written only ebooks reached the
guarded apply path at all. ADR-0069 then let an owner decide a recording, and
those answers went nowhere: a decision formed against an audiobook case carried
`application='recorded_only'`, and narrators was withheld from the
write-capable destination set entirely.

Two facts closed that gap. Audiobookshelf stores ebooks and audiobooks as one
`book` media type served by one metadata update family, `PATCH
/api/items/{id}/media`, with no audio-specific branch, so a narrator cast, a
series with a sequence, an ASIN, a description and a language are the same
request on either medium. And write authority arrives only after a read-only
round earns it: the 0.20.0 measurement and the owner-decided history F45
accumulated are that evidence, reported on #968.

The disposable F14 canary proved the shapes empirically before anything was
lifted, exercising each of those destinations on a generated audiobook item in
its own book-media library through capability discovery, plan, one-field claim,
one request and read-back on both pinned images -- the 2.26.0 floor and the
2.36.0 latest stable. The receipt digests are cited in the pull request.

## Decision

The original guarded envelope contained fifteen book-media destinations, the same
set on both media. F46a (#1047/#1056) extends that set to sixteen with `explicit`.
Its exact boolean representation may set true from a qualified adult-product
positive; false or missing source evidence never clears a value. Existing
consents lapse because the whole destination-set digest changes; an owner must
grant new consent, and automatic application remains off by default.
Narrators is written as `metadata.narrators`, a plain
list of strings, stored whole and read back whole, and an absent cast reads as
no value. A service that answers narrators in any other shape disables that
destination alone -- the capability reads `field_unavailable` and the library
keeps every other field.

A decision formed against an audiobook case carries `application='apply'` and
takes exactly the ebook path: per-library consent, plan, preflight, one-field
claim, destination-minimal request, read-back verification, durable audit and
the existing recovery contract. There is no audiobook consent tier, no
audiobook planner and no audiobook write path.

A `recorded_only` document retained from F45 is readable history and nothing
else. Only an audiobook case may carry one; it is stale against an ebook case;
no owner may submit one as a new command on either medium; the pending-write
queries exclude it at the SQL seam, the continuous scheduler skips it, and the
planner refuses it by name because reaching the planner at all is a defect. It
is never migrated into an applied decision, and the reason the owner reads
beside it becomes historical: "recorded, not applied -- decided before
audiobook writes were available".

Adding a destination changes the consent envelope, so ADR-0039's rule applies
unchanged: the envelope identity digests the whole destination set, every
consent granted before this build lapses, nothing is planned or claimed under
one, and Activity discloses narrators' readiness and re-offers consent before
any write is scheduled. No schema version moves; `human-decision-v2` is
unchanged.

Chapters, duration, media files, tag files and embedded writes stay excluded.
ADR-0073 governs the later qualified audiobook cover and subtitle operations.
Its paired title/subtitle effect extends the existing claim and audit with the
exact companion, both decisions and both consents; one PATCH must verify both
values before the pair is reported applied. It does not create a second
audiobook lifecycle or weaken ambiguous-response recovery.

## Consequences

Positive. The owner's audiobook decisions reach the library through one
envelope, one path and one set of guarantees, and narrators becomes decidable
and writable on both media.

Negative. Every library needs exactly one fresh consent after this build is
installed, because the envelope identity moved. Decisions retained under F45
never apply; an owner who wants those values written decides them again.

Neutral and deferred. F46 and F46a own automatic audiobook authority under
ADR-0073. The F20 rich-metadata manifest keeps narrators in its
excluded destinations, and re-pins the behaviour-source digests this change
edits without any option being re-measured.

## Alternatives considered

A separate audiobook consent tier. Rejected: it adds a concept the owner has to
learn for a distinction the service does not make, since both media are one
media type behind one update family.

Migrating retained `recorded_only` decisions into `apply`. Rejected: the
posture is bound into the decision's request digest and therefore its identity,
so a migration would rewrite immutable facts, which ADR-0013's posture forbids.

An adapter-specific workaround for a rejected destination shape. Rejected by
the governing expansion boundary: `field_unavailable` already disables exactly
one field and records the finding, which is the honest outcome.

## Validation

The `audiobookshelf-field-effect-v3` canary receipt on both pinned images, with
the audiobook item's per-destination capability and outcome; a fake-service
end-to-end test in the `required-installed` cohort covering decision, plan,
apply, read-back, audit, idempotent re-apply and an ambiguous response that is
reconciled rather than retried; the envelope-invalidation test; the browser
history test; and documentation integrity.
