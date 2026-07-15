# Contract-derived mutation testing

Apply this policy when delivery changes hostile parsing, normalization,
selection precedence, identity/lineage validation, credentials, or resource
accounting. Derive mutations from the frozen contract before reading fixtures.

Prefer a compact table/property cohort proving rejection and valid near-miss
behavior. Select only applicable dimensions:

- truncated, empty, missing, duplicated, reordered, conflicting, null, wrong-
  type, malformed, false, or unknown input;
- prefix/suffix and delimiter confusion, namespace/case/encoding variants;
- strict parse then recovery, including cumulative work accounting;
- ranked/excluded candidates, fallback order, and duplicate identities;
- digest, lineage, ownership, citation, and cardinality tampering;
- exact configured and one-unit-over cumulative limits; and
- ASCII/Unicode surrounding whitespace for credentials.

Assert observable contracts, not internals. Every rejection proves no partial
success, mutation, credentialed request, or retained artifact. Valid near-
misses remain accepted so hardening does not narrow compatibility silently.

The writer adds the smallest reusable cohort before the evidence-before-review
gate. The reviewer with the corresponding lens then tries at least one
supported mutation outside that cohort from its isolated raw packet. Bind run
results to the exact target/ref, artifact and environment; an invalidating edit
makes them stale and requires rerun before targeted continuation.
