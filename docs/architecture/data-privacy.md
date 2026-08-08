# Data privacy

## Review preparation progress

The authenticated preparation surface exposes only configured connection and
library names, an opaque scan identifier, a closed state, checked/total/ready/
blocked counts, and whether another owner request was coalesced. It never
projects item titles, authors, filenames, media paths, provider queries or
responses, credentials, failure text, or report values. Normal review items
remain governed by the existing private/no-store Library and Attention
contracts. Persisted operational work rows use existing opaque target, file,
report, proposal, and case identities with exact source/producer identity and
fixed stage and failure codes. One bounded current work ledger per discovered
eligible item is reconciled in place; run history does not accumulate.


0.10 minimizes retained data to one neutral evidence document per distinct
observation. It retains the configured logical connection/library names,
target item identifier, current title, opaque file identity and digest, bounded
local field claims and results, fact-local opening excerpts of at most 280
characters, bounded source/DOM occurrence and rear-spine provenance, and
bounded provider candidate title/identifier fields and opaque provider-local
work identities. Open Library may add only its closed finite rich-evidence
allowlist: bounded upstream identities and provenance, selected identifiers and
classifications, subjects, edition characteristics, contributor identity and
role, edition-work relation, first-publication date, and cover IDs.
Google Books retains only bounded volume IDs, qualified claims, format and
edition signals, and an explicit unresolved work-link marker. Its
`publishedDate` value is typed evidence, never a selectable date claim.
The target projection retains exactly 16 bounded Audiobookshelf destinations.
Provider cover rows retain only safe opaque source/work/edition identities,
typed designation, allowlisted edition context, artifact digests, dimensions,
media type, and preview references.
Provider discovery additionally retains opaque lane identities, exact local
claim references, normalized ISBN families, and provider-local edition/work
identities. It retains no request URL or raw lane payload. Same-provider lane
convergence remains one lineage and cannot count as independent corroboration.
One ProposalSet per distinct exact report/policy result retains only the report
ID, derived target fingerprint, fixed policy version, bounded candidate values,
assessments, evidence references, hashed correlation groups, reasons,
dependencies, and an optional selected alternative ID. It does not duplicate
report excerpts, provider evidence records, target current-state storage, or
cover provenance.

It does not retain credentials, Google Books API keys, authorization headers,
provider cover URLs, the Open Library
operational contact email, endpoint paths, private filesystem paths, raw
target/provider responses, arbitrary upstream properties or links, arbitrary
EPUB fragments, inventory, cover bytes, raw reviewer streams, reasoning,
malformed output, qualification profile paths, auth state, job files, request
bodies, logs, or support bundles. The contact email is also excluded from
report and manifest identities and every application fingerprint. Fixed stderr
diagnostics contain no rejected values.

The isolated child returns one bounded result for one EPUB session; neither its
input snapshot nor source body is retained after the report projection.

Reports and ProposalSets are append-only and provide no retention, pruning,
purge, export, or deletion feature. Operators must account for that deliberate limitation
when selecting the database location and backup policy.

Review cases are likewise append-only, retaining only the already bounded
ProposalSet projection and target facts needed for exact currentness. Admission
does not add reviewer identity, decision text, raw evidence, or service secrets.

Shadow review adds append-only sanitized canonical packets, validated canonical
results, path-free executable/model/profile/policy identities, attempt and
dispatch process facts, and canonical bounded failure diagnostics. The
packet uses only the opaque ReviewCase ID, current field projections, declared
candidate/evidence identities, and the bounded report facts those candidates
already reference. It excludes connection/library/item IDs, file identities,
paths, credentials, target/provider payloads, raw CLI streams, chain-of-thought,
and arbitrary diagnostics. Public inspection aggregates only category, closed
component/stage, optional retryability/currentness, and count; it omits packets,
results, process facts, attempt identifiers and ordinals, and legacy digests.

Policy Preview additionally retains append-only canonical deterministic outcome
sets. They contain only opaque case/report/proposal/target fingerprints,
policy/profile/generation and fixed rule/catalogue identities, closed
dispositions, action/candidate identities, and evidence/combination references
already admitted to the bounded case. They add no raw evidence, title, item,
path, credential, provider response, model output, human identity, or decision
text. Authenticated samples request at most 50 records and are truncated at one
MiB without adding a pagination or export surface. There is no per-outcome
purge, retention, or selective export command.

The complete application SQLite database—including shadow packets and advice—
is exposed to whoever can read a backup. Store it and its backups as private
application state with owner-scoped access and encrypted-at-rest backup
controls. Version 0.10 has no per-record purge, pruning, selective export, or
retention command; recovery and deletion operate on the complete database.

Prior and intermediate development databases are never imported, migrated, or
rewritten. Operators may retain them offline, subject to the same data-handling
obligations, but recovery for the current increment uses a clean database and
supported report regeneration.

The retained local vocabulary includes bounded title/subtitle, creator/contributor
roles, series/sequence, ISBN, dates, publisher/imprint, description, language,
and contextual subject evidence; it retains no paths or source bodies.
Edition and rights remain outside that public report vocabulary. Their private
qualification labels and exploratory aggregates are not retained in reports or
committed to the repository.
