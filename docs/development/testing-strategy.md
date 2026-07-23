# Testing strategy

The public gate installs the package non-editably from the lockfile and runs the
entire `unittest` suite. The suite covers:

- closed reports, canonical serialization, stable claim/report references,
  title normalization, and ISBN applicability;
- strict configuration, duplicate/reference rejection, and no implicit inputs;
- scoped Audiobookshelf item reading, response bounds, root mounts,
  `relPath`/`path` agreement, multiple files, and opaque identity across reorder;
- isolated EPUB traversal, expansion limits, malformed markup, external/entity
  exclusion, timeout, child failure, invalid IPC, and output bounds;
- provider input selection, no-input zero egress, one-request cardinality,
  observable cap overflow, cross-provider claim-reference uniqueness, sparse
  candidates, all-or-nothing malformed handling, and identity order;
- exact two-table DELETE-mode schema, side-effect-free existing-file
  validation, no-follow connection binding, Linux owner-directory lock and
  verified private-alias binding, raced configured-sidecar isolation, alias
  cleanup, append-only history, replay, scope-checked successor pointers,
  restart readback, corrupt-row rejection, and rollback;
- rejection before unauthorized parser/provider/publication activity,
  transaction-bound currentness drift, exact-byte snapshot binding, title
  privacy rejection, redaction, and absence of product/media writes; and
- installed package shape, distribution exclusions, generated-schema drift,
  static dependency direction, and retired-surface absence.

The activation demonstration builds a wheel, installs it without editable
source, runs the suite, and exercises the CLI against disposable loopback
target, synthetic media, private secrets, and an absent database. No live
library or provider is used in development or CI.

Hostile-input tests name a supported external actor and reachable boundary.
Typed in-process collaborators are not attacker-controlled unless the product
explicitly introduces an untrusted extension seam.
