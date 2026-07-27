# Testing strategy

The public gate checks the lockfile and runs the canonical exact-artifact
qualifier:

```bash
python3 scripts/qualify_exact_artifact.py \
  --source-sha "$(git rev-parse HEAD)"
```

That procedure requires a clean checkout at the supplied source SHA, snapshots
the committed source, disables the `uv` build and install cache, validates the
wheel's bounded archive and `RECORD` before installation, seeds a new
environment from all locked dependency groups, and installs the explicit wheel
pathname without dependency resolution. It proves the wheel and installed
distribution payloads are byte-identical, validates the installer-updated
`RECORD`, rechecks the whole-wheel hash, and runs the entire public suite with
the isolated environment's interpreter. It then checks the PEP 610 install
origin and invokes the installed console command from outside the checkout.
Its path-free JSON evidence binds the source SHA, wheel SHA-256, payload
SHA-256, and console-output SHA-256. A dirty or different source, ambiguous or
changed wheel, invalid archive/record, reused destination, unlocked dependency,
origin mismatch, payload mismatch, stale test install, or non-installed
import/command fails closed. The installed console proof performs exact-ID
report and ProposalSet creation, concurrent admission, replay, restart-backed
get, successor/historical projection, and nonactionable retirement against a
disposable loopback target and synthetic EPUB.

The suite covers:

- closed reports, canonical serialization, stable claim/report references,
  title normalization, and occurrence-local ISBN applicability, including
  explicit non-ISBN scheme and URI controls across package, opening, and rear
  evidence;
- strict configuration, duplicate/reference rejection, and no implicit inputs;
- scoped Audiobookshelf item reading, response bounds, root mounts,
  `relPath`/`path` agreement, all 16 destination states/currentness, multiple
  files, and opaque identity across reorder;
- isolated EPUB traversal, expansion limits, malformed markup, external/entity
  exclusion, timeout, child failure, invalid IPC, and output bounds;
- provider input selection, no-input zero egress, one-request cardinality,
  observable cap overflow, cross-provider claim-reference uniqueness, sparse
  candidates, exact ISBN versus complete-author work fallback, fail-closed work
  qualification, typed taxonomy/publication scope, all-or-nothing malformed
  handling, and identity order;
- Open Library exact ISBN-family Search admission, canonical edition/work
  request order, shared one-second pacing, finite rich evidence, terminal
  failure mapping, identified metadata/cover requests, cover-failure isolation,
  provider-order/contact privacy, and the explicit five-item batch envelope;
- cover-gallery provider/aggregate bounds, safe provenance, timeout/rejection
  states, visible truncation, and digest deduplication with all provenance;
- exact clean-state DELETE-mode report/ProposalSet/ReviewCase schema, side-effect-free existing-file
  validation, no-follow connection binding, Linux owner-directory lock and
  competing-process serialization, root-to-parent chain validation, verified
  private-alias binding, raced
  configured-sidecar isolation, final-precommit parent replacement rollback,
  alias cleanup, append-only history, replay, scope-checked successor
  pointers, restart readback, corrupt-row rejection, and rollback;
- fixed-policy exact 16-result enumeration, complete author/narrator lists and
  atomic series, independent corroboration, accepted-versus-unresolved ISBN
  applicability, year-only/date consistency, populated subjective-field
  closure, complete cover-gallery selection, deterministic ProposalSet replay,
  stale-report no-insert, transaction-local report/current-pointer recheck,
  exact evaluator recomputation before review admission, synthetic ProposalSet
  rejection, and zero provider calls during proposal creation;
- rejection before unauthorized parser/provider/publication activity,
  transaction-bound currentness drift, exact-byte snapshot binding, title
  privacy rejection, redaction, and absence of product/media writes; and
- installed package shape, distribution exclusions, generated-schema drift,
  static dependency direction, and retired-surface absence.

The exact-artifact suite includes the activation demonstration and exercises the
CLI against a disposable loopback target, synthetic media, private secrets, and
an absent database. Every synthetic EPUB member has a fixed timestamp so the
probe identity does not depend on the qualification wall clock. No live library
or provider is used in development or CI.

Hostile-input tests name a supported external actor and reachable boundary.
Typed in-process collaborators are not attacker-controlled unless the product
explicitly introduces an untrusted extension seam.
