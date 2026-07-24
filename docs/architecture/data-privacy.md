# Data privacy

0.4 minimizes retained data to one neutral evidence document per distinct
observation. It retains the configured logical connection/library names,
target item identifier, current title, opaque file identity and digest, bounded
local field claims and results, fact-local opening excerpts of at most 280
characters, bounded source/DOM occurrence and rear-spine provenance, and
bounded provider candidate title/identifier fields and opaque provider-local
work identities.

It does not retain credentials, authorization headers, endpoint paths, private
filesystem paths, raw target/provider responses, arbitrary EPUB fragments,
inventory, cover bytes, reviewer data, job history, request bodies, logs, or
support bundles. Fixed stderr diagnostics contain no rejected values.

The isolated child returns one bounded result for one EPUB session; neither its
input snapshot nor source body is retained after the report projection.

Reports are append-only and 0.4 provides no retention, pruning, purge, export,
or deletion feature. Operators must account for that deliberate limitation
when selecting the database location and backup policy.

Prior and intermediate development databases are never imported, migrated, or
rewritten. Operators may retain them offline, subject to the same data-handling
obligations, but recovery for a current 0.5 increment uses a clean database and
supported report regeneration.

The retained local vocabulary includes bounded title/subtitle, creator/contributor
roles, series/sequence, ISBN, dates, publisher/imprint, description, language,
and contextual subject evidence; it retains no paths or source bodies.
