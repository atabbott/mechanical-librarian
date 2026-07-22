# DUX fixture and regression corpus

## Purpose and boundary

The DUX corpus preserves production-relevant review scenarios from the
[DUX extraction record](dux-extraction.md) as generated, test-only contract
facts. It is not a copy of the prototype, a product data format, or a runtime
resource. Production code must not import the loader, locate the corpus, or
package any corpus file.

Every datum in the initial corpus was independently generated for Mechanical
Librarian. No prototype code, strings, fixture values, markup, browser state,
formatting, source symbols, report facts, counts, identifiers, paths, domains,
people, topology, or repository metadata was copied or adapted. The frozen
source revision appears only in the dedicated provenance fields required to
identify the behavior source.

## Location and schema

The registry is `tests/fixtures/dux/registry.json`; scenario records are the
other JSON files in that directory. `tests/support/dux_corpus.py` owns the
test-only schema and loader. Schema `dux-corpus-v2` is an internal test
compatibility contract, not a supported application API.

The registry has exactly three fields:

- `schema`: the corpus schema version;
- `scenario_files`: a sorted, unique list of every owned scenario file; and
- `coverage`: an exact category-to-scenario mapping.

Each scenario records:

- its schema, reserved scenario identity, coverage category, exact DUX
  behavior identity, and preserved invariant;
- source commit and tag, behavioral treatment
  `independently_reimplemented`, and fixture origin `generated`;
- licence conclusion `AGPL-3.0-or-later`, authorship as Mechanical Librarian
  contributors and repository history, and a null required notice because no
  extra notice applies; and
- the smallest typed input facts and expected outcome needed by a contract
  consumer.

The exact coverage categories are evidence-only input, no-ops, independent
siblings, and destructive cases. One scenario owns each category. Combining
meanings does not create an additional implicit coverage entry.

## Reserved synthetic namespace and recipes

Corpus identities use `dux-synthetic-` followed by a purpose token; scenario
files use the fixed two-digit form `dux-synthetic-NN.json`. Generated values
use lower-case purpose tokens such as `synthetic-title-alpha`. Sequence values
are short invented numerals. Scenario ordinals are allocation labels only and
do not reproduce source counts or ordering.

New data is made from these recipes:

1. choose the next unused scenario ordinal and one unowned manifest category;
2. construct the minimum invented target, snapshot, proposal, evidence, case,
   family, work, or choice labels under the reserved namespace;
3. use invented field values with no relationship to a person, publication,
   library, provider, service, or source fixture;
4. express only typed contract inputs and outcomes, never presentation,
   renderer, browser, log, report, or runtime output; and
5. serialize with the loader's canonical JSON function and validate the entire
   registry before review.

Do not derive a replacement token reversibly from private or prototype data.
If a behavior cannot be represented with wholly invented values, stop and use
the separately governed irreversible-sanitization process rather than adding
it to this generated corpus.

## Validation and diagnostics

The loader reads and validates the registry and every owned file before it
returns any scenario. Validation rejects unknown or missing keys, incompatible
versions, absent provenance or licence facts, duplicate identities or files,
orphan files, incomplete or duplicate coverage, noncanonical files, and
malformed or oversized JSON. Returned
records, nested mappings, collections, coverage, and scenario order are
immutable.

Every root entry is owned explicitly; unexpected files, directories,
symlinks, and nested content fail validation. Each category also validates its
allowed kind, outcome and flags, bounded reserved values, and relevant
equality, cardinality, identity, and cross-field facts.

Adapter, connection, library, case, snapshot, evidence, proposal, and
sibling-case identities are validated by their exact domain value types,
including the 64-character internal-identifier bound. External item identities
retain their separate domain bound.

All files are canonical UTF-8 JSON: object keys are sorted, separators are
fixed, list order is deliberate, and exactly one final LF is present. Repeated
serialization must match the committed bytes. Golden values describe typed
contract facts and outcomes only.

Validation errors expose a stable bounded rule code plus a validated synthetic
scenario ID or file ordinal. They never echo rejected keys, values, parser
messages, filesystem locations, or raw exceptions.

The recursive audit rejects:

- personal, tilde/home, assignment-embedded, workspace, temporary, Windows,
  and UNC path shapes;
- domains, URLs and generic URI schemes, email addresses, IP addresses,
  credential labels, headers, cookies, passwords, keys, and tokens, including
  compact forms plus dot, underscore, hyphen, whitespace, colon, slash, and
  percent-encoded delimiter variants;
- report, database, browser, runtime, support-bundle, and production-state
  markers in the same delimiter-equivalent forms;
- production-looking UUID and publication-identifier shapes, plus common
  copyright markers;
- control characters, bidirectional controls, surrogates, and oversized text;
  and
- prototype presentation/runtime markers and known frozen-source literal
  families recorded as irreversible, source-safe deny patterns.

The mandated source commit and tag are allowlisted only at their exact
dedicated provenance locations; embedded occurrences elsewhere are rejected.
The repository does not retain a raw private or source-value blocklist, and
negative tests use newly generated unsafe sentinels.

## Consumers

Every scenario is consumed through an exact available typed domain seam. The
consumers prove evidence-only empty-case rejection,
`FieldProposal` no-change shape, display-only `ReviewGroup` sibling isolation,
and destructive exclusion from ordinary `ReviewCase` flow. They do not claim
candidate aggregation, selection, apply-plan, cover, or series behavior that
the current domain does not implement.

An extension must remain test-only, allocate exactly one registry category,
use an available typed consumer, and pass complete registry validation. Do not
add a production loader, resource lookup, network check, prototype checkout,
environment discovery, or hidden compatibility fallback.

For a compatible data correction, rewrite the affected scenario and registry
with canonical serialization and review the byte diff. For an incompatible
schema change, assign a new schema version, update the loader and every record
in one change, deterministically migrate all committed files, and retain tests
that reject the old version. Partial migrations are invalid.

## Review and qualification

A clean-room reviewer who did not generate a corpus change must compare the
behavior identity and typed outcome with Mechanical Librarian's governing
contracts, inspect every changed fixture byte, and confirm that no prototype
implementation/presentation material or user facts remain. Review evidence
records the generated-data conclusion, source provenance, licence conclusion,
authorship, notice result, sanitization/audit result, and any rejected material;
it must not record private deny-list values.

Qualification runs the repository's documented non-editable test command. The
focused corpus tests additionally demonstrate two identical serialization
runs, fail-closed invalid fixtures with non-sensitive diagnostics, current
typed consumers, environment independence, absence of production imports or
fixed corpus references, and corpus exclusion from the installed distribution
and a freshly built wheel. The wheel test uses the build-pinned setuptools
version from the development lock, disables build isolation, and supplies
offline mode with empty home and cache directories.

No supported container definition exists at this ticket's baseline. Built
container inspection is therefore explicitly deferred to container ticket #90
and F15a tracker #151. Those owners must inspect the built image and prove the
corpus and test support are absent before the container outcome closes. This
corpus change adds no container and claims no container evidence.
