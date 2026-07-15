# EPUB Fixture Materialization

The EPUB evidence fixture registry is
[`tests/fixtures/epub_package/manifest.json`](../../tests/fixtures/epub_package/manifest.json).
It contains the original F06 package cohort and the F07a opening-matter cohort;
do not create a parallel fixture corpus.

Materialize every registry entry classified as `materialized` from a checkout:

```console
python3 scripts/materialize_epub_fixtures.py
```

The command performs no network access and writes a flat disposable cohort to
the ignored `build/epub-fixtures/` directory. Use `--output PATH` for a different
empty directory and `--manifest PATH` to qualify a reordered or candidate
registry. The output contains one `<fixture-id>.epub` file per materialized
entry and a canonical filename-ordered `SHA256SUMS` index. The command refuses
a nonempty output directory so stale files cannot enter the cohort.

Generated EPUB files and indexes are qualification artifacts and must not be
committed. The registry declares fixture-count, per-file, and aggregate byte
limits. Each entry also records stable identity, materialized/virtual/linked
classification and reason, consumer ownership, expected top-level outcomes,
deterministic recipe, provenance, and SPDX identity. `virtual` entries remain
owned by bounded in-process qualification, while `linked` entries point to
process or environment evidence that is not a standalone EPUB. Linked
generators must match the repository-owned closed target allowlist; a candidate
manifest cannot introduce an arbitrary test or executable target. Virtual and
linked rows are bound to their exact owned target, so an alternate manifest
also cannot swap one allowlisted target into a different evidence row.
Materialization and qualification share one closed registry parser. Alternate
manifests fail on unknown or missing fields, duplicate identities, consumer
drift, unowned materialized recipes, or inconsistent outcome/exit pairs.

The F07a materialized cohort includes landmark-scope, ordinary recovered HTML,
invalid-encoding, encrypted-navigation, foreign-inclusion, unsupported-media,
per-resource byte/depth isolation, empty body-transition markers, recovered
void/implied-close structure, namespace-aliased foreign constructs, and
safe-sibling counterexamples. Linked executable matrix entries cover exact and
rejection boundaries for document, node, text, navigation-link, excerpt, and
canonical-report limits. The
installed-artifact test creates a disposable virtual environment, installs the
built wheel and its pinned runtime dependencies, and invokes its real console
script so both parent and isolated child resolve from that wheel.

The repository-authored packages contain synthetic bibliographic facts and no
copied publication content. Re-running the materializer from clean directories
must produce identical bytes and index content regardless of manifest order,
locale, timezone, home directory, or hash seed.
