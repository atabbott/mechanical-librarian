# Testing strategy

The public gate installs the package non-editably from the lockfile and runs the
entire `unittest` suite. It then runs the canonical exact-artifact qualifier:

```bash
python scripts/qualify_exact_artifact.py --source-sha "$(git rev-parse HEAD)"
```

That procedure requires a clean checkout at the supplied source SHA, disables
the `uv` build and install cache, builds exactly one wheel in a new directory,
installs its explicit pathname into a new isolated environment, proves the
wheel payload and installed distribution payload are byte-identical, checks the
PEP 610 install origin, and invokes the installed console command from outside
the checkout. Its path-free JSON evidence binds the source SHA, wheel SHA-256,
payload SHA-256, and console-output SHA-256. A dirty or different source,
ambiguous wheel, reused destination, origin mismatch, payload mismatch, or
non-installed import/command fails closed.

The suite covers:

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
- Open Library exact ISBN-family Search admission, canonical edition/work
  request order, shared one-second pacing, finite rich evidence, terminal
  failure mapping, identified metadata/cover requests, cover-failure isolation,
  provider-order/contact privacy, and the explicit five-item batch envelope;
- exact three-table DELETE-mode schema, side-effect-free existing-file
  validation, no-follow connection binding, Linux owner-directory lock and
  root-to-parent chain validation, verified private-alias binding, raced
  configured-sidecar isolation, final-precommit parent replacement rollback,
  alias cleanup, append-only history, replay, scope-checked successor
  pointers, restart readback, corrupt-row rejection, and rollback;
- rejection before unauthorized parser/provider/publication activity,
  transaction-bound currentness drift, exact-byte snapshot binding, title
  privacy rejection, redaction, and absence of product/media writes; and
- installed package shape, distribution exclusions, generated-schema drift,
  static dependency direction, and retired-surface absence.

The activation demonstration runs the suite and exercises the CLI against a
disposable loopback target, synthetic media, private secrets, and an absent
database. Its installed wheel is prepared by the same exact-artifact procedure.
No live library or provider is used in development or CI.

Hostile-input tests name a supported external actor and reachable boundary.
Typed in-process collaborators are not attacker-controlled unless the product
explicitly introduces an untrusted extension seam.
